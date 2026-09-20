package main

import (
	"context"
	"errors"
	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/sqs"
	st "github.com/aws/aws-sdk-go-v2/service/sqs/types"
	"testing"
)

type fakeStore struct {
	err    error
	seen   map[string]bool
	writes int
}

func (s *fakeStore) Put(_ context.Context, e Event) error {
	if s.err != nil {
		return s.err
	}
	if !s.seen[e.EventID] {
		s.seen[e.EventID] = true
		s.writes++
	}
	return nil
}

type fakeQueue struct {
	deletes int
	err     error
}

func (q *fakeQueue) DeleteMessage(context.Context, *sqs.DeleteMessageInput, ...func(*sqs.Options)) (*sqs.DeleteMessageOutput, error) {
	q.deletes++
	return &sqs.DeleteMessageOutput{}, q.err
}
func TestHandle(t *testing.T) {
	valid := `{"event_id":"1","device_id":"r1","event_type":"up","payload":{}}`
	for _, tc := range []struct {
		name, body                       string
		failStore, failDelete, wantError bool
		deletes                          int
	}{
		{"success", valid, false, false, false, 1}, {"retry", valid, true, false, true, 0},
		{"malformed", "{", false, false, true, 0}, {"missing", `{}`, false, false, true, 0},
		{"ack failure", valid, false, true, true, 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := &fakeStore{seen: map[string]bool{}}
			q := &fakeQueue{}
			if tc.failStore {
				s.err = errors.New("throttled")
			}
			if tc.failDelete {
				q.err = errors.New("network")
			}
			err := handle(context.Background(), s, q, "queue", st.Message{Body: aws.String(tc.body), ReceiptHandle: aws.String("r")})
			if (err != nil) != tc.wantError || q.deletes != tc.deletes {
				t.Fatalf("error=%v deletes=%d", err, q.deletes)
			}
		})
	}
	s := &fakeStore{seen: map[string]bool{}}
	q := &fakeQueue{}
	for i := 0; i < 2; i++ {
		if err := handle(context.Background(), s, q, "queue", st.Message{Body: aws.String(valid)}); err != nil {
			t.Fatal(err)
		}
	}
	if s.writes != 1 || q.deletes != 2 {
		t.Fatal("duplicate was not acknowledged")
	}
}
