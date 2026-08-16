"""Development-only local Node simulation; grants no computer access."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Isabella.Nodes import Node, NodeManager, NodeRegistry, NodeStatus, NodeType, TrustState, load_nodes_config
from Isabella.Protocol import encode
from Isabella.Protocol import MessageType, NodeIdentity, NodeType as ProtocolNodeType, ProtocolMessage
from Isabella.Transport import WebSocketNodeClient


CAPABILITIES_BY_TYPE = {
    NodeType.MOBILE: ("notifications",),
    NodeType.SECONDARY_PC: ("text_input",),
    NodeType.HOME: ("sensors",),
    NodeType.HELMET: ("imu",),
    NodeType.EMBEDDED: ("sensors",),
    NodeType.DISPLAY: ("display",),
    NodeType.SENSOR: ("sensors",),
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulate an untrusted local ISABELLA Node")
    parser.add_argument("--type", choices=[item.value for item in CAPABILITIES_BY_TYPE], default="MOBILE")
    parser.add_argument("--connect", metavar="WS_URL", help="Connect to a local ISABELLA WebSocket server")
    parser.add_argument("--token-file", type=Path, help="Local bearer token file for command tests")
    parser.add_argument("--skill", default="scheduler.list", help="Allowlisted zero-argument Skill used in connected mode")
    args = parser.parse_args()
    node_type = NodeType(args.type)
    available_capabilities = set(load_nodes_config()["known_capabilities"])
    with tempfile.TemporaryDirectory(prefix="isabella-node-") as directory:
        config = {"enabled": True, "identity_file": str(Path(directory) / "identity.json"), "registry_file": str(Path(directory) / "registry.json"), "offline_after_seconds": 30, "known_capabilities": ["notifications", "text_input", "sensors", "imu", "display"]}
        manager = NodeManager(config, registry=NodeRegistry())
        node = Node(f"simulated.{node_type.value.lower()}", f"Simulated {node_type.value}", node_type, NodeStatus.CONNECTING, capabilities=CAPABILITIES_BY_TYPE[node_type], trust=TrustState.UNTRUSTED)
        manager.register(node)
        hello = node.hello("primary.local")
        encoded = encode(hello, available_capabilities=available_capabilities)
        manager.heartbeat(node.node_id)
        print(f"NODE={node.node_id} TYPE={node.node_type.value} STATUS={node.status.value} TRUST={node.trust.value}")
        print(f"PROTOCOL=1.0 HELLO_BYTES={len(encoded)} CAPABILITIES={','.join(node.capabilities)}")
        print("ACCESS_GRANTED=false")
        if args.connect:
            token = args.token_file.read_text(encoding="utf-8").strip() if args.token_file else None
            protocol_identity = NodeIdentity(node.node_id, ProtocolNodeType.SMARTPHONE if node_type is NodeType.MOBILE else node.protocol_identity().node_type, node.name, capabilities=node.capabilities)
            client = WebSocketNodeClient(args.connect, protocol_identity, token=token, available_capabilities=available_capabilities)
            welcome = client.connect()
            heartbeat = client.heartbeat(1)
            client.send(ProtocolMessage(MessageType.STATUS, node.node_id, "primary.local", {"status": "ONLINE"}))
            request = ProtocolMessage(MessageType.COMMAND_REQUEST, node.node_id, "primary.local", {"skill_id": args.skill, "arguments": {}})
            client.send(request)
            result = client.receive()
            client.disconnect()
            print(f"WELCOME={welcome.type.value} HEARTBEAT={heartbeat.type.value} COMMAND={result.payload.get('status')} GOODBYE=SENT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
