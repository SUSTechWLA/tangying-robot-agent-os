"""Local authenticated peer advice for the existing spatial runner ports.

Peer reports can reduce redundant candidate scores. They never enter the local
CoverageLedger, suppress all candidates, grant execution or prove completion.
"""
import copy
import hashlib
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request

from .spatial_mission import CoverageLedger

_MAX_RESPONSE = 4 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("local exchange redirects are forbidden")


class PeerExchangeClient:
    """Talk only to this robot's loopback admin API, never a peer control API."""

    def __init__(self, url, token, *, timeout=2.0):
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"}
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {"", "/"} or not parsed.port):
            raise ValueError("exchange admin URL must be literal loopback HTTP with a port")
        if type(token) is not str or len(token) < 32 or any(char.isspace() for char in token):
            raise ValueError("private exchange admin token required")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 5:
            raise ValueError("exchange timeout must be 0..5 seconds")
        self.url, self._token, self.timeout = url.rstrip("/"), token, timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def _request(self, route, payload):
        raw = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
        if len(raw) > 65536:
            raise ValueError("exchange request exceeds byte budget")
        request = urllib.request.Request(self.url + "/v1/exchange/" + route, data=raw,
                                         headers={"Authorization": "Bearer " + self._token,
                                                  "Content-Type": "application/json"})
        with self._opener.open(request, timeout=self.timeout) as response:
            body = response.read(_MAX_RESPONSE + 1)
        if len(body) > _MAX_RESPONSE:
            raise ValueError("exchange response exceeds byte budget")
        return json.loads(body)

    def coverage_view(self, query):
        return self._request("coverage-view", query)

    def publish(self, request):
        return self._request("publish", request)


def coverage_query(ledger, *, mission_revision, basis, max_age_ms=None, limit=1000):
    """Build the remote query from the exact local CoverageLedger scope."""
    if not isinstance(ledger, CoverageLedger) or not ledger.targets or not ledger.geometry_id:
        raise ValueError("peer coverage requires a declared ledger scope and geometry")
    if (type(mission_revision) is not str or not mission_revision
            or type(basis) is not dict or set(basis) != {
                "worldId", "frameId", "transformRevision", "mapId", "mapRevision"}
            or any(type(value) is not str or not value for value in basis.values())
            or basis["transformRevision"] != ledger.world_revision):
        raise ValueError("explicit mission revision and registered ledger basis required")
    age = ledger.max_age_ms if max_age_ms is None else max_age_ms
    if type(age) is not int or not 1 <= age <= ledger.max_age_ms:
        raise ValueError("peer observation age cannot exceed local ledger age budget")
    if type(limit) is not int or not 1 <= limit <= 1000 or len(ledger.targets) > 4096:
        raise ValueError("bounded exchange query target and inbox budgets required")
    return {"schemaVersion": "agent.coverage.query.v1", "missionId": ledger.mission_id,
            "missionRevision": mission_revision, "geometryId": ledger.geometry_id,
            "basis": copy.deepcopy(basis), "targetIds": [str(i) for i in sorted(ledger.targets)],
            "maxAgeMs": age, "limit": limit}


class PeerAdvisedExplorer:
    """Drop-in explorer_factory result; hints only rescore certified candidates.

    A fresh complete snapshot is read every proposal. A retained feed cursor is
    not treated as evidence: network errors, expiry, gaps and incomplete snapshots
    leave original scores intact. Local observations remain the completion source.
    """

    def __init__(self, explorer, client, query, *, weight=0.25, clock_ms=None, journal=None):
        if type(weight) not in (int, float) or not math.isfinite(weight) or not 0 <= weight <= 1:
            raise ValueError("peer advice weight must be 0..1")
        self.explorer, self.client, self.query = explorer, client, copy.deepcopy(query)
        self.weight, self.clock_ms, self.journal = weight, clock_ms or (lambda: int(time.time() * 1000)), journal

    def _advice(self):
        view = self.client.coverage_view(copy.deepcopy(self.query))
        if (type(view) is not dict or view.get("schemaVersion") != "agent.coverage.view.v1"
                or view.get("advisoryOnly") is not True or view.get("grantsExecution") is not False
                or view.get("physicalCompletion") is not False or view.get("completeRead") is not True
                or any(view.get(key) != self.query[key] for key in (
                    "missionId", "missionRevision", "geometryId", "basis"))):
            raise ValueError("peer view is incomplete or belongs to a different scope")
        timestamp = view.get("evaluatedAtMs")
        now = self.clock_ms()
        if (type(timestamp) is not int or timestamp > now + 1000
                or now - timestamp > self.query["maxAgeMs"]):
            raise ValueError("peer view is stale")
        proposed = view.get("proposedCoveredTargetIds")
        if (type(proposed) is not list or len(proposed) > len(self.query["targetIds"])
                or any(type(value) is not str for value in proposed)
                or len(set(proposed)) != len(proposed)
                or not set(proposed) <= set(self.query["targetIds"])):
            raise ValueError("peer view targets exceed declared scope")
        return {int(value) for value in proposed}, view

    def propose(self, start_pose, *, excluded_targets=(), observed_targets=()):
        proposal = copy.deepcopy(self.explorer.propose(
            start_pose, excluded_targets=excluded_targets, observed_targets=observed_targets))
        diagnostic = {"available": False, "advisoryOnly": True, "weight": self.weight}
        try:
            targets, view = self._advice()
            diagnostic.update(available=True, proposedTargets=len(targets), cursor=view.get("cursor"))
            if self.journal is not None:
                self.journal.append("peer_coverage_advice", view)
        except (OSError, ValueError, TypeError, KeyError) as error:
            diagnostic["reason"] = type(error).__name__
        else:
            for candidate in proposal.get("candidates", []):
                values = candidate.get("expectedObservedVoxelIds", candidate.get("visibleUnknownVoxelIds", []))
                overlap = len(set(values) & targets) / max(1, len(set(values)))
                score = candidate["score"]
                candidate["score"] = score - self.weight * overlap * (abs(score) + 1)
            proposal.get("candidates", []).sort(key=lambda item: (-item["score"], item["targetId"]))
        proposal.setdefault("diagnostics", {})["peerAdvice"] = diagnostic
        return proposal


def publish_capture(client, query, packet, peer_ids, *, ttl_ms=5000):
    """Export only actual capture voxel IDs as advisory; no ledger mutation.

    Use the same captured packet already validated by the local map/ledger port.
    Request IDs bind capture evidence and recipient; retries preserve publication.
    """
    if (packet.get("worldFrameRevision") != query["basis"]["transformRevision"]
            or packet.get("geometryId") != query["geometryId"]):
        raise ValueError("capture geometry does not match exchange task scope")
    if type(ttl_ms) is not int or not 1 <= ttl_ms <= query["maxAgeMs"]:
        raise ValueError("capture TTL must fit the admitted freshness budget")
    classified = packet.get("classified")
    if type(classified) is not dict or any(type(key) is not int or type(value) is not int
                                         or value not in (0, 100) for key, value in classified.items()):
        raise ValueError("only measured classified voxel captures may be published")
    identifiers = sorted(str(value) for value in classified if str(value) in query["targetIds"])
    if not identifiers:
        return []
    if (type(peer_ids) not in (list, tuple) or not 1 <= len(peer_ids) <= 256
            or len(set(peer_ids)) != len(peer_ids)):
        raise ValueError("bounded unique peer identities required")
    payload = {"schemaVersion": "agent.coverage.advisory.v1", "missionId": query["missionId"],
               "missionRevision": query["missionRevision"], "geometryId": query["geometryId"],
               "observedAtMs": packet["observedAtMs"], "observedTargetIds": identifiers}
    receipts = []
    for peer in peer_ids:
        identity = json.dumps([query, packet["evidenceId"], peer], sort_keys=True).encode()
        request = {"requestId": "capture-" + hashlib.sha256(identity).hexdigest(), "peerId": peer,
                   "stream": "coverage/" + packet["sourceId"], "kind": "observation", "ttlMs": ttl_ms,
                   "basis": copy.deepcopy(query["basis"]), "payload": payload}
        receipts.append(client.publish(request))
    return receipts
