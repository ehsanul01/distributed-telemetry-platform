package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb"
	dt "github.com/aws/aws-sdk-go-v2/service/dynamodb/types"
	"github.com/aws/aws-sdk-go-v2/service/sqs"
	st "github.com/aws/aws-sdk-go-v2/service/sqs/types"
)

type Event struct {
	EventID   string                     `json:"event_id"`
	DeviceID  string                     `json:"device_id"`
	EventType string                     `json:"event_type"`
	Payload   map[string]json.RawMessage `json:"payload"`
}
type Store interface {
	Put(context.Context, Event) error
}
type Queue interface {
	DeleteMessage(context.Context, *sqs.DeleteMessageInput, ...func(*sqs.Options)) (*sqs.DeleteMessageOutput, error)
}
type dynamoStore struct {
	client *dynamodb.Client
	table  string
}

func (s dynamoStore) Put(ctx context.Context, e Event) error {
	payload, err := json.Marshal(e.Payload)
	if err != nil {
		return err
	}
	_, err = s.client.PutItem(ctx, &dynamodb.PutItemInput{
		TableName: aws.String(s.table), ConditionExpression: aws.String("attribute_not_exists(event_id)"),
		Item: map[string]dt.AttributeValue{
			"event_id":   &dt.AttributeValueMemberS{Value: e.EventID},
			"device_id":  &dt.AttributeValueMemberS{Value: e.DeviceID},
			"event_type": &dt.AttributeValueMemberS{Value: e.EventType},
			"payload":    &dt.AttributeValueMemberS{Value: string(payload)},
		},
	})
	var duplicate *dt.ConditionalCheckFailedException
	if errors.As(err, &duplicate) {
		return nil
	}
	return err
}
func handle(ctx context.Context, store Store, queue Queue, url string, msg st.Message) error {
	var event Event
	if err := json.Unmarshal([]byte(aws.ToString(msg.Body)), &event); err != nil {
		return fmt.Errorf("invalid message: %w", err)
	}
	if event.EventID == "" || event.DeviceID == "" || event.EventType == "" || event.Payload == nil {
		return errors.New("missing required event fields")
	}
	if err := store.Put(ctx, event); err != nil {
		return err
	}
	_, err := queue.DeleteMessage(ctx, &sqs.DeleteMessageInput{QueueUrl: aws.String(url), ReceiptHandle: msg.ReceiptHandle})
	return err
}
func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
func main() {
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" {
		c := http.Client{Timeout: 3 * time.Second}
		res, err := c.Get("http://127.0.0.1:8080/healthz")
		if err != nil {
			os.Exit(1)
		}
		res.Body.Close()
		if res.StatusCode != 200 {
			os.Exit(1)
		}
		return
	}
	if err := run(); err != nil {
		slog.Error("worker stopped", "error", err)
		os.Exit(1)
	}
}
func run() error {
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()
	n, err := strconv.Atoi(env("WORKER_CONCURRENCY", "4"))
	if err != nil || n < 1 || n > 64 {
		return errors.New("WORKER_CONCURRENCY must be 1..64")
	}
	cfg, err := config.LoadDefaultConfig(ctx, config.WithRegion(env("AWS_DEFAULT_REGION", "us-east-1")))
	if err != nil {
		return err
	}
	endpoint := os.Getenv("AWS_ENDPOINT_URL")
	queue := sqs.NewFromConfig(cfg, func(o *sqs.Options) {
		if endpoint != "" {
			o.BaseEndpoint = aws.String(endpoint)
		}
	})
	db := dynamodb.NewFromConfig(cfg, func(o *dynamodb.Options) {
		if endpoint != "" {
			o.BaseEndpoint = aws.String(endpoint)
		}
	})
	lookupCtx, stop := context.WithTimeout(ctx, 10*time.Second)
	result, err := queue.GetQueueUrl(lookupCtx, &sqs.GetQueueUrlInput{QueueName: aws.String(env("QUEUE_NAME", "telemetry-events"))})
	stop()
	if err != nil {
		return err
	}
	url := aws.ToString(result.QueueUrl)
	table := env("TABLE_NAME", "telemetry-events")
	store := dynamoStore{db, table}
	var ready atomic.Bool
	check := func() {
		c, done := context.WithTimeout(ctx, 3*time.Second)
		defer done()
		_, e := db.DescribeTable(c, &dynamodb.DescribeTableInput{TableName: aws.String(table)})
		if e == nil {
			_, e = queue.GetQueueAttributes(c, &sqs.GetQueueAttributesInput{QueueUrl: aws.String(url), AttributeNames: []st.QueueAttributeName{st.QueueAttributeNameQueueArn}})
		}
		ready.Store(e == nil)
	}
	check()
	go func() {
		ticker := time.NewTicker(5 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				check()
			}
		}
	}()
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		if !ready.Load() {
			http.Error(w, "dependencies unavailable", 503)
			return
		}
		w.Write([]byte("ok\n"))
	})
	server := &http.Server{Addr: ":8080", Handler: mux, ReadHeaderTimeout: 3 * time.Second}
	go func() {
		if e := server.ListenAndServe(); e != nil && !errors.Is(e, http.ErrServerClosed) {
			slog.Error("health server", "error", e)
			cancel()
		}
	}()
	var wg sync.WaitGroup
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for ctx.Err() == nil {
				batch, e := queue.ReceiveMessage(ctx, &sqs.ReceiveMessageInput{QueueUrl: aws.String(url), MaxNumberOfMessages: 1, WaitTimeSeconds: 20, VisibilityTimeout: 30})
				if e != nil {
					if ctx.Err() != nil {
						return
					}
					slog.Error("receive failed", "error", e)
					select {
					case <-ctx.Done():
						return
					case <-time.After(time.Second):
					}
					continue
				}
				for _, msg := range batch.Messages {
					// Bound processing below visibility timeout; unacknowledged messages retry.
					job, done := context.WithTimeout(ctx, 10*time.Second)
					e = handle(job, store, queue, url, msg)
					done()
					if e != nil {
						slog.Warn("message left for retry", "message_id", aws.ToString(msg.MessageId), "error", e)
					}
				}
			}
		}()
	}
	<-ctx.Done()
	wg.Wait()
	shutdown, done := context.WithTimeout(context.Background(), 5*time.Second)
	defer done()
	return server.Shutdown(shutdown)
}
