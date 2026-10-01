package main

import (
	"context"
	"encoding/json"
	st "github.com/aws/aws-sdk-go-v2/service/sqs/types"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb"
)

func TestDynamoConditionalWrite(t *testing.T) {
	for _, tc := range []struct {
		name    string
		status  int
		body    string
		wantErr bool
	}{
		{"stored", 200, `{}`, false},
		{"duplicate", 400, `{"__type":"com.amazonaws.dynamodb.v20120810#ConditionalCheckFailedException","message":"exists"}`, false},
		{"throttle", 400, `{"__type":"com.amazonaws.dynamodb.v20120810#ProvisionedThroughputExceededException","message":"throttled"}`, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				var body map[string]interface{}
				if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
					t.Error(err)
				}
				if body["ConditionExpression"] != "attribute_not_exists(event_id)" {
					t.Error("missing conditional write")
				}
				if body["TableName"] != "events" {
					t.Error("wrong table")
				}
				item := body["Item"].(map[string]interface{})
				if item["event_id"].(map[string]interface{})["S"] != "event-1" {
					t.Error("wrong event id")
				}
				w.Header().Set("Content-Type", "application/x-amz-json-1.0")
				w.WriteHeader(tc.status)
				w.Write([]byte(tc.body))
			}))
			defer server.Close()
			client := dynamodb.NewFromConfig(aws.Config{Region: "us-east-1", Credentials: credentials.NewStaticCredentialsProvider("test", "test", ""), RetryMaxAttempts: 1}, func(o *dynamodb.Options) { o.BaseEndpoint = aws.String(server.URL) })
			store := dynamoStore{client, "events"}
			err := store.Put(context.Background(), Event{EventID: "event-1", DeviceID: "r1", EventType: "up", Payload: map[string]json.RawMessage{"value": json.RawMessage(`1`)}})
			if (err != nil) != tc.wantErr {
				t.Fatalf("unexpected error: %v", err)
			}
		})
	}
}

// Exercise message decoding, the real DynamoDB SDK request and duplicate acknowledgement.
func TestTimestampsAndDuplicatePreservation(t *testing.T) {
	for _, tc := range []struct{ name, received, want string }{
		{"UTC receipt", `,"received_at":"2026-09-30T10:00:00.123456Z"`, "2026-09-30T10:00:00.123456Z"},
		{"offset normalized", `,"received_at":"2026-09-30T12:00:00+02:00"`, "2026-09-30T10:00:00Z"},
		{"legacy missing receipt", "", ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var mu sync.Mutex
			var saved map[string]map[string]string
			writes := 0
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				var request struct {
					ConditionExpression string
					Item                map[string]map[string]string
				}
				if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
					t.Error(err)
					w.WriteHeader(400)
					return
				}
				if request.ConditionExpression != "attribute_not_exists(event_id)" {
					t.Error("write must remain conditional")
				}
				mu.Lock()
				defer mu.Unlock()
				writes++
				w.Header().Set("Content-Type", "application/x-amz-json-1.0")
				if saved != nil && request.ConditionExpression == "attribute_not_exists(event_id)" {
					w.WriteHeader(400)
					w.Write([]byte(`{"__type":"ConditionalCheckFailedException","message":"exists"}`))
					return
				}
				saved = request.Item
				w.Write([]byte(`{}`))
			}))
			defer server.Close()
			client := dynamodb.NewFromConfig(aws.Config{Region: "us-east-1", Credentials: credentials.NewStaticCredentialsProvider("test", "test", ""), RetryMaxAttempts: 1}, func(o *dynamodb.Options) { o.BaseEndpoint = aws.String(server.URL) })
			store := dynamoStore{client, "events"}
			queue := &fakeQueue{}
			body := `{"event_id":"event-1","device_id":"r1","event_type":"up","payload":{}` + tc.received + `}`
			before := time.Now().UTC()
			if err := handle(context.Background(), store, queue, "queue", st.Message{Body: aws.String(body)}); err != nil {
				t.Fatal(err)
			}
			after := time.Now().UTC()
			mu.Lock()
			firstProcessed := saved["processed_at"]["S"]
			received, hasReceived := saved["received_at"]
			mu.Unlock()
			processed, err := time.Parse(time.RFC3339Nano, firstProcessed)
			if err != nil || processed.Before(before) || processed.After(after) || firstProcessed[len(firstProcessed)-1:] != "Z" {
				t.Fatalf("invalid processing timestamp: %q", firstProcessed)
			}
			if (tc.want != "") != hasReceived || received["S"] != tc.want {
				t.Fatalf("unexpected received_at: %v", received)
			}
			// A later duplicate must not fill a legacy receipt or replace either timestamp.
			duplicate := `{"event_id":"event-1","device_id":"r1","event_type":"up","payload":{},"received_at":"2030-01-01T00:00:00Z"}`
			if err := handle(context.Background(), store, queue, "queue", st.Message{Body: aws.String(duplicate)}); err != nil {
				t.Fatal(err)
			}
			mu.Lock()
			defer mu.Unlock()
			if saved["processed_at"]["S"] != firstProcessed || saved["received_at"]["S"] != tc.want {
				t.Fatal("duplicate overwrote timestamps")
			}
			if writes != 2 || queue.deletes != 2 {
				t.Fatal("both original and duplicate must be acknowledged")
			}
		})
	}
}

func TestMalformedReceiptIsNotAcknowledged(t *testing.T) {
	store := &fakeStore{seen: map[string]bool{}}
	queue := &fakeQueue{}
	msg := st.Message{Body: aws.String(`{"event_id":"1","device_id":"r1","event_type":"up","payload":{},"received_at":"yesterday"}`)}
	if err := handle(context.Background(), store, queue, "queue", msg); err == nil {
		t.Fatal("expected malformed timestamp error")
	}
	if queue.deletes != 0 || store.writes != 0 {
		t.Fatal("malformed timestamp should retry without storage or acknowledgement")
	}
}
