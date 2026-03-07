"""
Vulnerability Database — CVE and known vulnerability lookup for RF protocols.
"""
import json
import logging
import os

logger = logging.getLogger("signalpirate.vuln_db")


class VulnDB:
    """CVE / vulnerability database manager."""

    def __init__(self, data_dir=None):
        self.vulnerabilities = []
        if data_dir is None:
            data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
        self.data_dir = data_dir

    def load(self):
        """Load vulnerabilities from JSON file."""
        path = os.path.join(self.data_dir, "vulnerabilities.json")
        try:
            with open(path, "r") as f:
                data = json.load(f)
            # Support both raw list and {"vulnerabilities": [...]} format
            if isinstance(data, list):
                self.vulnerabilities = data
            else:
                self.vulnerabilities = data.get("vulnerabilities", [])
            logger.info(f"Loaded {len(self.vulnerabilities)} vulnerabilities")
        except Exception as e:
            logger.error(f"Failed to load vulnerability database: {e}")

    def match_signal(self, signal_data, protocol_info=None):
        """Check if a signal matches any known vulnerability."""
        matches = []

        if protocol_info:
            # Check related CVEs from protocol info
            related_cves = protocol_info.get("related_cves", [])
            for vuln in self.vulnerabilities:
                if vuln["id"] in related_cves:
                    matches.append(vuln)

            # Check by security level
            security = protocol_info.get("security", "")
            security_level = protocol_info.get("security_level", "")

            if security == "none":
                # Add fixed-code generic vulnerability
                for vuln in self.vulnerabilities:
                    if vuln["id"] == "FIXED-CODE-GENERIC" and vuln not in matches:
                        matches.append(vuln)

            if "keeloq" in protocol_info.get("name", "").lower():
                for vuln in self.vulnerabilities:
                    if "keeloq" in vuln.get("protocol", "").lower() and vuln not in matches:
                        matches.append(vuln)

        return matches if matches else None

    def search(self, query):
        """Search vulnerabilities by ID, title, or description."""
        query = query.lower()
        results = []
        for vuln in self.vulnerabilities:
            if (query in vuln["id"].lower() or
                query in vuln["title"].lower() or
                query in vuln["description"].lower() or
                query in vuln.get("attack_type", "").lower()):
                results.append(vuln)
        return results

    def get_all(self):
        """Return all vulnerabilities."""
        return self.vulnerabilities

    def get_by_severity(self, severity):
        """Get vulnerabilities by severity level."""
        return [v for v in self.vulnerabilities if v.get("severity") == severity]

    def get_stats(self):
        """Return vulnerability database statistics."""
        severities = {}
        for vuln in self.vulnerabilities:
            sev = vuln.get("severity", "unknown")
            severities[sev] = severities.get(sev, 0) + 1
        return {
            "total": len(self.vulnerabilities),
            "by_severity": severities,
        }
