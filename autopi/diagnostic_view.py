class DiagnosticView:
    """Displays a diagnostic reader's results - both the raw jargon and the
    AI's plain-English translation. Works with ANY DiagnosticReader
    (polymorphism): freeze frame, pending codes, readiness, etc."""

    def __init__(self, reader):
        # reader: any DiagnosticReader subclass
        self._reader = reader

    def show(self):
        print("")
        print("=================================================")
        print("  ", self._reader.name())
        print("=================================================")
        print("Reading from car...")

        result = self._reader.report()   # {mode, raw, plain_english}

        print("")
        print("--- RAW DATA (the technical truth) ---")
        raw = result["raw"]
        if raw is None:
            print("Nothing reported.")
        elif isinstance(raw, dict):
            for key in raw:
                print("  ", key, ":", raw[key])
        elif isinstance(raw, list):
            for item in raw:
                print("  ", item)
        else:
            print("  ", raw)

        print("")
        print("--- PLAIN ENGLISH (what your car is saying) ---")
        print(result["plain_english"])
        print("=================================================")
        input("Press Enter to go back...")