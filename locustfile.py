import os
import uuid
import random
from locust import HttpUser, FastHttpUser, task, between

"""
Simple Locust load test for the OPTIKORM backend.
Behavior per simulated user:
- on_start: register a fresh admin account and create one nutrient, one fish and one feed.
- tasks: list nutrients/fish/feeds and call calculation endpoint.

Configuration via environment variables:
- LOCUST_BASE_PATH: base path for API (default: '' which means api is at /api/)
- LOCUST_PASSWORD: password used when registering accounts (default: 'locustpass')
- CREATE_RESOURCES: '1' to create nutrient/fish/feed in on_start (default '1')

Notes:
- This test registers a unique admin per virtual user to avoid collisions and to ensure test data exists.
- For heavy load runs you may prefer to pre-create test data and only perform read/calculate operations.
- The API is expected to be at /api/ path (e.g., /api/auth/register/, /api/nutrients/, etc.)
"""

BASE_PATH = os.getenv('LOCUST_BASE_PATH', '')
PASSWORD = os.getenv('LOCUST_PASSWORD', 'locustpass')
CREATE_RESOURCES = os.getenv('CREATE_RESOURCES', '1') == '1'


def rand_username(prefix='locust_admin'):
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class OptikormUser(HttpUser):
    # Keep the realistic user with small wait by default. For generator stress tests
    # set the ENV var CREATE_RESOURCES=0 and/or run the lightweight `SimpleReader` below.
    # When experimenting with max RPS, you can set LOCUST_WAIT_MIN=0 and LOCUST_WAIT_MAX=0
    wait_time = between(int(os.getenv('LOCUST_WAIT_MIN', 0)), int(os.getenv('LOCUST_WAIT_MAX', 0)))

    def on_start(self):
        # Initialize attributes
        self.token = None
        self.nutrient_id = None
        self.fish_id = None
        self.feed_id = None
        
        # Register an admin user for this simulated user
        username = rand_username()
        data = {'username': username, 'password': PASSWORD, 'role': 'admin'}
        
        # Fix: use /api/auth/register/ path
        with self.client.post(f"{BASE_PATH}api/auth/register/", json=data, catch_response=True) as resp:
            if resp.status_code in (200, 201):
                try:
                    self.token = resp.json().get('token')
                except Exception as e:
                    print(f"Failed to parse register response: {e}")
                    self.token = None
            else:
                # If registration failed (maybe username exists), try login
                self.token = None

        if not self.token:
            # Try to login with same credentials (in case account already exists)
            with self.client.post(f"{BASE_PATH}api/auth/login/", json={'username': username, 'password': PASSWORD}, catch_response=True) as resp:
                if resp.status_code == 200:
                    try:
                        self.token = resp.json().get('token')
                    except Exception as e:
                        print(f"Failed to parse login response: {e}")
                        self.token = None

        if not self.token:
            # As a last resort, continue without auth (tests will be limited)
            print(f"Warning: No token obtained for user {username}")
            self.client.headers.pop('Authorization', None)
            return

        # Set Authorization header for subsequent requests
        self.client.headers.update({"Authorization": f"Token {self.token}"})

        # Create basic resources (1 nutrient, 1 fish, 1 feed) to allow calculation
        if CREATE_RESOURCES:
            # Create nutrient
            r = self.client.post(f"{BASE_PATH}api/nutrients/", json={'name': 'Protein', 'unit': 'g/kg'})
            if r.status_code in (200, 201):
                self.nutrient_id = r.json().get('id')

            # Create fish with nutrient mapping
            if self.nutrient_id:
                fish_payload = {'name': f'Fish_{uuid.uuid4().hex[:4]}', 'nutrients': {str(self.nutrient_id): '50.0'}}
                r2 = self.client.post(f"{BASE_PATH}api/fish/", json=fish_payload)
                if r2.status_code in (200, 201):
                    self.fish_id = r2.json().get('id')

            # Create feed with nutrient mapping
            if self.nutrient_id:
                feed_payload = {'name': f'Feed_{uuid.uuid4().hex[:4]}', 'price': '10.0', 'nutrients': {str(self.nutrient_id): '50.0'}}
                r3 = self.client.post(f"{BASE_PATH}api/feeds/", json=feed_payload)
                if r3.status_code in (200, 201):
                    self.feed_id = r3.json().get('id')

    @task(3)
    def list_resources(self):
        # Simple GET requests
        self.client.get(f"{BASE_PATH}api/nutrients/")
        self.client.get(f"{BASE_PATH}api/fish/")
        self.client.get(f"{BASE_PATH}api/feeds/")

    @task(2)
    def calculate(self):
        # Call calculate endpoint if we have a fish id
        if self.fish_id:
            payload = {'fish_selections': [{'fish_id': int(self.fish_id), 'weight': 100.0}]}
            self.client.post(f"{BASE_PATH}api/calculate/", json=payload)
        else:
            # Fallback: attempt a calculate with invalid data to measure error path
            self.client.post(f"{BASE_PATH}api/calculate/", json={'fish_selections': []})

    @task(1)
    def ping_api_root(self):
        self.client.get(f"{BASE_PATH}api/")


class SimpleReader(FastHttpUser):
    """Very lightweight user that repeatedly requests list endpoints with no waits.

    Use this class (e.g. with Locust -u / -r or in distributed mode) to saturate the
    load generator and push RPS up. It does not register or create resources.
    """
    wait_time = between(0, 0)

    @task
    def read_basic(self):
        # Single GET — minimal client-side processing
        self.client.get(f"{BASE_PATH}api/nutrients/")
