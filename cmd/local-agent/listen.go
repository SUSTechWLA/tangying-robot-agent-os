package main

import (
	"fmt"
	"net"
	"strings"
)

// The console's listen-address policy.
//
// The console has no user accounts, and it now issues a session token over
// plaintext HTTP. On loopback that is a defensible boundary: only processes on
// this machine can reach the port, and those could already read the database
// directly. Bound to a network interface the same design is not defensible — the
// token crosses the wire in the clear on every request, and the console's read
// routes serve the camera, the maps and every task's full text to anyone who can
// reach the port.
//
// So a non-loopback bind is refused unless the operator asks for it by name. The
// refusal says what it would expose rather than pointing at a flag: this is the
// message an operator sees at the moment they were about to do the wrong thing
// for a reasonable reason ("I want to watch it from my laptop").

// loopbackListen reports whether addr provably binds only the loopback
// interface.
//
// It answers false for anything it cannot prove: an empty host (":8787" binds
// every interface), a hostname, and an unparsable address. Guessing "probably
// fine" is the wrong direction for the one setting that decides who can reach a
// robot's controls.
func loopbackListen(addr string) bool {
	host, _, err := net.SplitHostPort(addr)
	if err != nil {
		// No port, or unparsable. Fall through to the host check so a bare
		// "127.0.0.1" is still recognised; ListenAndServe will reject it for the
		// missing port on its own.
		host = addr
	}
	host = strings.TrimSpace(host)
	if host == "" {
		return false
	}
	if strings.EqualFold(host, "localhost") {
		return true
	}
	ip := net.ParseIP(host)
	if ip == nil {
		// A hostname that is not "localhost": resolving it here would make the
		// answer depend on DNS at startup, which is neither stable nor a thing
		// this check should trust.
		return false
	}
	return ip.IsLoopback()
}

// remoteConsoleRefusal builds the error for a refused non-loopback bind.
func remoteConsoleRefusal(addr string) error {
	return fmt.Errorf(
		"拒绝在 %s 上提供服务：控制台没有账号体系，会话令牌走明文 HTTP，"+
			"绑到网络接口等于把相机画面、地图和全部任务文本交给任何能连到这个端口的人。"+
			"确实需要时用 --allow-remote-console（或 LOCAL_ALLOW_REMOTE=1）显式开启，"+
			"并在前面放一个做鉴权的反向代理",
		addr,
	)
}

// remoteConsoleWarning is printed when the operator did opt in.
//
// It repeats the exposure instead of staying quiet: an acknowledgement given
// once, months ago, should not be invisible at the moment it matters.
func remoteConsoleWarning(addr string) string {
	return fmt.Sprintf(
		"警告：控制台绑在 %s（非环回）。它没有账号体系，会话令牌与全部读接口都在明文 HTTP 上，请确认前面有做鉴权的反向代理。",
		addr,
	)
}
