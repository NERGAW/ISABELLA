"""Development-only local Node simulation; grants no computer access."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Isabella.Nodes import Node, NodeManager, NodeRegistry, NodeStatus, NodeType, TrustState
from Isabella.Protocol import encode


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
    args = parser.parse_args()
    node_type = NodeType(args.type)
    with tempfile.TemporaryDirectory(prefix="isabella-node-") as directory:
        config = {"enabled": True, "identity_file": str(Path(directory) / "identity.json"), "registry_file": str(Path(directory) / "registry.json"), "offline_after_seconds": 30, "known_capabilities": ["notifications", "text_input", "sensors", "imu", "display"]}
        manager = NodeManager(config, registry=NodeRegistry())
        node = Node(f"simulated.{node_type.value.lower()}", f"Simulated {node_type.value}", node_type, NodeStatus.CONNECTING, capabilities=CAPABILITIES_BY_TYPE[node_type], trust=TrustState.UNTRUSTED)
        manager.register(node)
        hello = node.hello("primary.local")
        encoded = encode(hello, available_capabilities=set(config["known_capabilities"]))
        manager.heartbeat(node.node_id)
        print(f"NODE={node.node_id} TYPE={node.node_type.value} STATUS={node.status.value} TRUST={node.trust.value}")
        print(f"PROTOCOL=1.0 HELLO_BYTES={len(encoded)} CAPABILITIES={','.join(node.capabilities)}")
        print("ACCESS_GRANTED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

