// edge-exchange is a data-only companion for a Local Agent or Fleet Worker.
// It owns no robot control connection and cannot dispatch physical commands.
package main

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	x "github.com/SUSTechWLA/tangying-robot-agent-os/core/exchangecontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/exchange"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
)

type peerConfig struct {
	Identity          x.Identity `json:"identity"`
	URL               string     `json:"url"`
	IncomingTokenFile string     `json:"incomingTokenFile"`
	OutgoingTokenFile string     `json:"outgoingTokenFile"`
}
type config struct {
	Identity          x.Identity   `json:"identity"`
	Database          string       `json:"database"`
	Capacity          int          `json:"capacity"`
	Listen            string       `json:"listen"`
	AdminListen       string       `json:"adminListen"`
	AdminTokenFile    string       `json:"adminTokenFile"`
	TLSCertFile       string       `json:"tlsCertFile"`
	TLSKeyFile        string       `json:"tlsKeyFile"`
	PeerCAFile        string       `json:"peerCaFile,omitempty"`
	AllowLoopbackHTTP bool         `json:"allowLoopbackHttp,omitempty"`
	Peers             []peerConfig `json:"peers"`
}
type prepared struct {
	config    config
	incoming  map[string]string
	admin     string
	transport *exchange.HTTPTransport
	tls       *tls.Config
}

func main() {
	path := flag.String("config", "", "path to edge exchange JSON configuration")
	check := flag.Bool("check-config", false, "validate configuration without opening sockets or a journal")
	flag.Parse()
	p, err := loadConfig(*path)
	if err != nil {
		log.Fatal(err)
	}
	if *check {
		fmt.Printf("edge exchange configuration ready: agent=%s peers=%d\n", p.config.Identity.AgentID, len(p.config.Peers))
		return
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	if err := run(ctx, p); err != nil {
		log.Fatal(err)
	}
}

func secret(path string) (string, error) {
	info, err := os.Stat(path)
	if err != nil {
		return "", err
	}
	if !info.Mode().IsRegular() || info.Mode().Perm()&0o077 != 0 || info.Size() > 4096 {
		return "", errors.New("token files must be regular, private (0600), and bounded")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	value := strings.TrimSpace(string(raw))
	if len(value) < 32 || strings.ContainsAny(value, " \t\n\r") {
		return "", errors.New("tokens must contain at least 32 non-whitespace bytes")
	}
	return value, nil
}

func loopbackAddress(address string) bool {
	host, _, err := net.SplitHostPort(address)
	if err != nil {
		return false
	}
	return host == "127.0.0.1" || host == "::1"
}

func loadConfig(path string) (*prepared, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	raw, err := io.ReadAll(io.LimitReader(file, 65537))
	if err != nil {
		return nil, err
	}
	if len(raw) > 65536 {
		return nil, errors.New("exchange configuration exceeds 65536 bytes")
	}
	if err := x.ValidateJSON(raw); err != nil {
		return nil, err
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	var c config
	if err := decoder.Decode(&c); err != nil {
		return nil, err
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		return nil, errors.New("invalid trailing configuration")
	}
	if err := c.Identity.Validate(); err != nil {
		return nil, err
	}
	if !filepath.IsAbs(c.Database) || c.Capacity < 1 || c.Capacity > 100000 {
		return nil, errors.New("absolute database path and capacity 1..100000 required")
	}
	if !loopbackAddress(c.AdminListen) {
		return nil, errors.New("admin listener must use literal loopback")
	}
	if _, _, err := net.SplitHostPort(c.Listen); err != nil {
		return nil, err
	}
	if c.Listen == c.AdminListen {
		return nil, errors.New("receive and admin listeners must be different")
	}
	if c.AllowLoopbackHTTP && !loopbackAddress(c.Listen) {
		return nil, errors.New("development HTTP must bind literal loopback")
	}
	if len(c.Peers) < 1 || len(c.Peers) > 256 {
		return nil, errors.New("configure 1..256 peers")
	}
	p := &prepared{config: c, incoming: map[string]string{}}
	p.admin, err = secret(c.AdminTokenFile)
	if err != nil {
		return nil, fmt.Errorf("admin token: %w", err)
	}
	tokens := map[string]bool{p.admin: true}
	destinations := map[string]exchange.Destination{}
	for _, peer := range c.Peers {
		if err := peer.Identity.Validate(); err != nil {
			return nil, err
		}
		if _, exists := p.incoming[peer.Identity.AgentID]; exists || peer.Identity.AgentID == c.Identity.AgentID {
			return nil, errors.New("duplicate or self peer identity")
		}
		incoming, err := secret(peer.IncomingTokenFile)
		if err != nil {
			return nil, fmt.Errorf("incoming peer token: %w", err)
		}
		if tokens[incoming] {
			return nil, errors.New("incoming peer tokens and admin token must be distinct")
		}
		tokens[incoming] = true
		outgoing, err := secret(peer.OutgoingTokenFile)
		if err != nil {
			return nil, fmt.Errorf("outgoing peer token: %w", err)
		}
		if outgoing == p.admin {
			return nil, errors.New("admin token cannot be sent to a peer")
		}
		p.incoming[peer.Identity.AgentID] = incoming
		destinations[peer.Identity.AgentID] = exchange.Destination{URL: peer.URL, Token: outgoing}
	}
	// Validate after collecting every peer so configuration order cannot hide a
	// cross-peer credential leak. A symmetric token for the same peer remains
	// compatible; sending another peer's inbound credential would let the
	// destination impersonate that peer on this receiver.
	for destinationID, destination := range destinations {
		for incomingID, incoming := range p.incoming {
			if incomingID != destinationID && destination.Token == incoming {
				return nil, errors.New("outgoing token must not reuse another peer's incoming token")
			}
		}
	}
	clientTLS := &tls.Config{MinVersion: tls.VersionTLS13}
	if c.PeerCAFile != "" {
		raw, err := os.ReadFile(c.PeerCAFile)
		if err != nil {
			return nil, err
		}
		roots := x509.NewCertPool()
		if !roots.AppendCertsFromPEM(raw) {
			return nil, errors.New("peer CA contains no certificates")
		}
		clientTLS.RootCAs = roots
	}
	client := &http.Client{Timeout: 5 * time.Second, Transport: &http.Transport{TLSClientConfig: clientTLS, MaxIdleConns: 32, IdleConnTimeout: 30 * time.Second}}
	p.transport, err = exchange.NewHTTPTransport(client, destinations, c.AllowLoopbackHTTP)
	if err != nil {
		return nil, err
	}
	if !c.AllowLoopbackHTTP {
		certificate, err := tls.LoadX509KeyPair(c.TLSCertFile, c.TLSKeyFile)
		if err != nil {
			return nil, err
		}
		leaf, err := x509.ParseCertificate(certificate.Certificate[0])
		if err != nil {
			return nil, err
		}
		now := time.Now()
		if now.Before(leaf.NotBefore) || !now.Before(leaf.NotAfter) {
			return nil, errors.New("exchange server certificate is not currently valid")
		}
		p.tls = &tls.Config{MinVersion: tls.VersionTLS13, Certificates: []tls.Certificate{certificate}}
	} else if c.TLSCertFile != "" || c.TLSKeyFile != "" {
		return nil, errors.New("development HTTP cannot ambiguously configure TLS certificates")
	}
	return p, nil
}

func run(ctx context.Context, p *prepared) error {
	if err := os.MkdirAll(filepath.Dir(p.config.Database), 0o700); err != nil {
		return err
	}
	store, err := sqlite.OpenExchange(p.config.Database, p.config.Capacity)
	if err != nil {
		return err
	}
	defer store.Close()
	if err := os.Chmod(p.config.Database, 0o600); err != nil {
		return err
	}
	identities := make([]x.Identity, 0, len(p.config.Peers))
	for _, peer := range p.config.Peers {
		identities = append(identities, peer.Identity)
	}
	s, err := exchange.New(ctx, p.config.Identity, identities, store)
	if err != nil {
		return err
	}
	receive, err := s.ReceiveHandler(p.incoming)
	if err != nil {
		return err
	}
	admin, err := s.AdminHandler(p.admin)
	if err != nil {
		return err
	}
	listener, err := net.Listen("tcp", p.config.Listen)
	if err != nil {
		return err
	}
	defer listener.Close()
	adminListener, err := net.Listen("tcp", p.config.AdminListen)
	if err != nil {
		return err
	}
	defer adminListener.Close()
	if p.tls != nil {
		listener = tls.NewListener(listener, p.tls)
	}
	server := &http.Server{Handler: receive, ReadHeaderTimeout: 3 * time.Second, ReadTimeout: 10 * time.Second, WriteTimeout: 10 * time.Second, IdleTimeout: 30 * time.Second, MaxHeaderBytes: 8192}
	adminServer := &http.Server{Handler: admin, ReadHeaderTimeout: 3 * time.Second, ReadTimeout: 10 * time.Second, WriteTimeout: 10 * time.Second, IdleTimeout: 30 * time.Second, MaxHeaderBytes: 8192}
	runCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	errorsCh := make(chan error, 2)
	go func() { errorsCh <- server.Serve(listener) }()
	go func() { errorsCh <- adminServer.Serve(adminListener) }()
	done := make(chan struct{})
	go func() {
		defer close(done)
		ticker := time.NewTicker(time.Second)
		defer ticker.Stop()
		lastPrune := time.Time{}
		for {
			if time.Since(lastPrune) >= time.Minute {
				if err := s.Prune(runCtx); err != nil && runCtx.Err() == nil {
					log.Printf("exchange retention failed: %v", err)
				}
				lastPrune = time.Now()
			}
			deliveries, err := s.Dispatch(runCtx, p.transport, 1000)
			if err != nil && runCtx.Err() == nil {
				log.Printf("exchange journal dispatch failed: %v", err)
			}
			for _, delivery := range deliveries {
				if delivery.Error != "" && runCtx.Err() == nil {
					log.Printf("exchange delivery pending: peer=%s message=%s error=%s", delivery.PeerID, delivery.MessageID, delivery.Error)
				}
			}
			select {
			case <-runCtx.Done():
				return
			case <-ticker.C:
			}
		}
	}()
	log.Printf("edge exchange ready: agent=%s receive=%s admin=%s", p.config.Identity.AgentID, listener.Addr(), adminListener.Addr())
	var result error
	select {
	case <-ctx.Done():
	case result = <-errorsCh:
		if errors.Is(result, http.ErrServerClosed) {
			result = nil
		}
	}
	cancel()
	shutdown, stop := context.WithTimeout(context.Background(), 5*time.Second)
	defer stop()
	if err := server.Shutdown(shutdown); err != nil && result == nil {
		result = err
	}
	if err := adminServer.Shutdown(shutdown); err != nil && result == nil {
		result = err
	}
	<-done
	return result
}
