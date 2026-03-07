"""
Protocol Database — Loads and queries the 433 MHz protocol encyclopedia.
"""
import json
import logging
import os

logger = logging.getLogger("signalpirate.protocol_db")


class ProtocolDB:
    """Protocol database manager."""

    def __init__(self, data_dir=None):
        self.protocols = []
        self._by_name = {}
        self._by_category = {}
        if data_dir is None:
            data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
        self.data_dir = data_dir

    def load(self):
        """Load protocols from JSON file."""
        path = os.path.join(self.data_dir, "protocols.json")
        try:
            with open(path, "r") as f:
                data = json.load(f)
            # Support both raw list and {"protocols": [...]} format
            if isinstance(data, list):
                self.protocols = data
            else:
                self.protocols = data.get("protocols", [])
            self._build_index()
            logger.info(f"Loaded {len(self.protocols)} protocols")
        except Exception as e:
            logger.error(f"Failed to load protocol database: {e}")

    def _build_index(self):
        """Build lookup indices."""
        self._by_name = {}
        self._by_category = {}
        for proto in self.protocols:
            name_lower = proto["name"].lower()
            self._by_name[name_lower] = proto
            cat = proto.get("category", "Unknown")
            if cat not in self._by_category:
                self._by_category[cat] = []
            self._by_category[cat].append(proto)

    def match_signal(self, signal_data):
        """Try to match a decoded signal to a protocol in the database."""
        model = signal_data.get("model", "").lower()
        if not model:
            return None

        # Direct name match
        if model in self._by_name:
            return self._by_name[model]

        # Partial match
        for name, proto in self._by_name.items():
            if name in model or model in name:
                return proto
            # Check device list
            for device in proto.get("devices", []):
                if model in device.lower() or device.lower() in model:
                    return proto

        return None

    def search(self, query):
        """Search protocols by name, category, or description."""
        query = query.lower()
        results = []
        for proto in self.protocols:
            if (query in proto["name"].lower() or
                query in proto.get("category", "").lower() or
                query in proto.get("description", "").lower() or
                any(query in d.lower() for d in proto.get("devices", []))):
                results.append(proto)
        return results

    def get_all(self):
        """Return all protocols."""
        return self.protocols

    def get_categories(self):
        """Return protocols grouped by category."""
        return self._by_category

    def get_by_name(self, name):
        """Get protocol by name."""
        return self._by_name.get(name.lower())
