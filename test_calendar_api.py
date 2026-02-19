import requests
import sys

def test_api(user_id):
    url = f"http://localhost:5000/api/reminders?user_id={user_id}"
    try:
        response = requests.get(url)
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    uid = sys.argv[1] if len(sys.argv) > 1 else "12345"
    test_api(uid)
