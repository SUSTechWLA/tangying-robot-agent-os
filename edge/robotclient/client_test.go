package robotclient_test

import (
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/robotclient"
)

func TestClientFailsClosedWithoutTLSOrExplicitDevMode(t *testing.T) {
	if _, err := robotclient.New(robotclient.Config{Address: "127.0.0.1:50051"}); err == nil {
		t.Fatal("client without TLS must require DevInsecure")
	}
	client, err := robotclient.New(robotclient.Config{Address: "127.0.0.1:50051", DevInsecure: true})
	if err != nil {
		t.Fatal(err)
	}
	_ = client.Close()
}
