import socket
import ipaddress
import requests
from concurrent.futures import ThreadPoolExecutor


class OllamaFinder:
    """Auto-discovers the Ollama server on the local subnet, so a changing
    laptop IP never breaks the AI. Scans ONLY the local subnet, ONLY the
    Ollama port (targeted + efficient, not a broad scan).

    Complexity: O(n) checks where n = ~254 subnet hosts, run concurrently
    via a thread pool (I/O-bound), so wall-clock ~1-2 seconds. O(n) memory."""

    OLLAMA_PORT = 11434

    def __init__(self, port=OLLAMA_PORT, timeout=0.3, max_workers=100):
        self.__port = port
        self.__timeout = timeout
        self.__max_workers = max_workers

    def __local_subnet_base(self):
        # Find THIS device's IP, derive the subnet (e.g. 192.168.68.)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))     # doesn't send data, just picks the interface
            my_ip = s.getsockname()[0]
        except Exception:
            my_ip = "127.0.0.1"
        finally:
            s.close()
        # Turn 192.168.68.42 into the /24 network 192.168.68.0/24
        return ipaddress.ip_network(my_ip + "/24", strict=False)

    def __port_open(self, ip):
        # True if the Ollama port is open on this IP (fast socket check)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.__timeout)
        result = sock.connect_ex((str(ip), self.__port))
        sock.close()
        return str(ip) if result == 0 else None

    def __is_ollama(self, ip):
        # Verify it's actually Ollama (not just something on that port)
        try:
            r = requests.get("http://" + ip + ":" + str(self.__port) + "/api/tags",
                             timeout=1)
            return r.status_code == 200
        except Exception:
            return False

    def find(self):
        # Returns the Ollama IP string, or None if not found.
        network = self.__local_subnet_base()
        hosts = list(network.hosts())    # ~254 addresses

        # Concurrent port checks (I/O-bound - threads collapse the wall-clock)
        candidates = []
        with ThreadPoolExecutor(max_workers=self.__max_workers) as pool:
            for result in pool.map(self.__port_open, hosts):
                if result is not None:
                    candidates.append(result)

        # Verify each open-port candidate is really Ollama
        for ip in candidates:
            if self.__is_ollama(ip):
                return ip
        return None