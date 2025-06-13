import json
import requests
import sseclient


ENDPOINT = "http://localhost:8080/register"   # adjust host/port


def listen_to_queue(endpoint: str = ENDPOINT, uuid_str: str = "uuid") -> None:
    """
    Open an SSE stream and mirror the logic of:

        const es = new EventSource("/register?request_id=my-uuid");
        es.onmessage = (e) => {
            const data = JSON.parse(e.data);
            console.log("Position changed:", data);
            if (data.queue_position === 0) {
                console.log("done")
            }
        };
    """
    endpoint = f"{endpoint}?request_id={uuid_str}"

    # 1. Start a streaming GET request
    response = requests.get(endpoint, stream=True, timeout=None)

    # 2. Wrap it as an SSE client
    client = sseclient.SSEClient(response)

    # 3. Consume events
    for event in client.events():
        # event.data is a str with exactly what the server sent after "data:"
        data = json.loads(event.data)

        print("Position changed:", data["data"], data["type"])

        if data["data"] == 0:
            print(f"{uuid_str} done")
            break   # leave the loop / stop listening


if __name__ == "__main__":
    import threading

    threads = []
    # Start the SSE stream in a separate thread
    for i in range(15):
        t = threading.Thread(target=listen_to_queue, args=(ENDPOINT, f"uuid-{i}"))
        threads.append(t)
        t.start()

    # Wait for all threads to finish
    for t in threads:
        t.join()