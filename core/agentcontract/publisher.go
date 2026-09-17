package agentcontract

import "context"

// Publisher is the optional capability of an agent that emits events.
//
// The Agent interface deliberately has no Publish method. Most of what an agent
// needs is already there — identity, capabilities, permissions, subscriptions,
// health, Execute, OnEvent — and an agent that only consumes events should not
// have to carry a publishing port it never uses. Adding one method per runtime
// capability would also turn the contract into something a new agent can no
// longer satisfy in a single small file, which is the property that makes
// "write an agent and register it" possible at all.
//
// Publishing is therefore opt-in: an agent declares it can publish by
// implementing this interface, and the runtime activates it.
//
// The runtime, not the composition root, performs the injection. That is the
// part learned by running the system rather than by testing it: the wiring named
// each agent's sink by hand, one agent was registered without one, and its
// findings reached a store and never reached the bus. No unit test caught it,
// because every unit test injected the sink itself. A capability the runtime
// hands out is a capability no call site can forget.
//
// SetPublish is called once, during runtime start, before any agent event is
// delivered.
type Publisher interface {
	SetPublish(publish func(ctx context.Context, event Event))
}
