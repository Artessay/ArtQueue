import json
import requests
import sseclient


ENDPOINT = "http://localhost:8080/register?request_id=my-uuid"   # adjust host/port


def listen_to_queue(endpoint: str = ENDPOINT) -> None:
    """
    Open an SSE stream and mirror the logic of:

        const es = new EventSource("/register?request_id=my-uuid");
        es.onmessage = (e) => {
            const data = JSON.parse(e.data);
            console.log("Position changed:", data.queue_position, data.event);
            if (data.queue_position === 0) {
                console.log("done")
            }
        };
    """
    # 1. Start a streaming GET request
    response = requests.get(endpoint, stream=True, timeout=None)

    # 2. Wrap it as an SSE client
    client = sseclient.SSEClient(response)

    # 3. Consume events
    for event in client.events():
        # event.data is a str with exactly what the server sent after "data:"
        data = json.loads(event.data)

        print("Position changed:", data["data"], data["type"])

        if data["queue_position"] == 0:
            print("done")
            break   # leave the loop / stop listening


if __name__ == "__main__":
    listen_to_queue()