package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

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
