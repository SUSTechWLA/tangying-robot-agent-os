package modelroute

import (
	"strings"
	"testing"
)

func TestCacheConfigIsExplicitAndStageLocal(t *testing.T) {
	values := map[string]string{"AGENT_PROVIDER": "openai", "AGENT_BASE_URL": "http://localhost:8000/v1", "AGENT_MODEL": "local", "AGENT_CACHE_MODE": "vllm", "AGENT_CACHE_NAMESPACE": "global-must-not-inherit"}
	endpoint := Resolve(values, Goal)
	if endpoint.Cache.EffectiveMode() != "off" {
		t.Fatal("inherited global cache settings")
	}
	values["AGENT_GOAL_CACHE_MODE"] = "vllm"
	values["AGENT_GOAL_CACHE_NAMESPACE"] = "lab-A"
	if err := Resolve(values, Goal).Validate(); err != nil {
		t.Fatal(err)
	}
	if Resolve(values, Recovery).Cache.EffectiveMode() != "off" {
		t.Fatal("cache settings crossed stages")
	}
	t.Setenv("AGENT_GOAL_PROVIDER", "openai")
	t.Setenv("AGENT_GOAL_BASE_URL", "http://localhost:8000/v1")
	t.Setenv("AGENT_GOAL_MODEL", "local")
	t.Setenv("AGENT_GOAL_CACHE_MODE", "openai")
	t.Setenv("AGENT_GOAL_CACHE_NAMESPACE", "lab-A")
	if e := Environment(Goal); e.Cache.Mode != "openai" || e.Cache.Namespace != "lab-A" || e.Validate() != nil {
		t.Fatalf("environment not wired: %+v", e)
	}
}

func TestInvalidCacheConfigFailsBeforeNetwork(t *testing.T) {
	for _, tc := range []struct{ stage, provider, mode, ns string }{
		{Goal, "openai", "bogus", "x"}, {Goal, "openai", "vllm", ""}, {Goal, "openai", "off", "x"},
		{Goal, "deterministic", "openai", "x"}, {Planning, "openai", "openai", "x"}, {Intent, "openai", "vllm", "x"}, {System, "openai", "vllm", "x"},
	} {
		t.Run(tc.stage+tc.provider+tc.mode+tc.ns, func(t *testing.T) {
			values := map[string]string{"AGENT_PROVIDER": tc.provider, "AGENT_BASE_URL": "http://localhost/v1", "AGENT_MODEL": "x", "AGENT_" + tc.stage + "_CACHE_MODE": tc.mode, "AGENT_" + tc.stage + "_CACHE_NAMESPACE": tc.ns}
			if Resolve(values, tc.stage).Validate() == nil {
				t.Fatal("invalid cache config accepted")
			}
		})
	}
	a := Assist{URL: "https://cloud.example/v1/assist", RobotID: "r", DeviceToken: "secret"}
	_, err := a.ClientFor(Endpoint{BaseURL: a.URL, Cache: CachePolicy{Mode: "vllm", Namespace: "lab"}})
	if err == nil || !strings.Contains(err.Error(), "does not accept cache hints") || strings.Contains(err.Error(), a.DeviceToken) {
		t.Fatalf("unsafe assist cache routing: %v", err)
	}
}
