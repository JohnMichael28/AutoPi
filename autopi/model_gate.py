"""One gate for the on-Pi AI model.

llama-server on the Pi answers ONE request at a time. The screens (AIClient)
and the talk-to-AutoPi copilot (LLMClient) used to call it at the same time;
the second request then sat in the server's queue, ran out its HTTP timeout,
and the driver saw "AI couldn't be reached" even though the model was fine,
just busy. Both clients now take this lock first, so requests line up
instead of timing each other out.
"""
import threading

LOCAL_MODEL_LOCK = threading.Lock()
