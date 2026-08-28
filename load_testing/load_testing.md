# Load Testing with Locust

This project includes a comprehensive Locust load testing script at `load_testing/locustfile.py` to simulate real-world usage and stress test all major shortcut and upstream features.

## Features Covered
- Static, dynamic, and user-dynamic shortcut creation and redirection
- Upstream check UI and unknown shortcut access
- Upstream cache resync, purge, and logs
- Google and random shortcut flows

## How to Run Load Tests

1. **Install Locust:**
   ```sh
   pip install locust
   ```

2. **Start your Flask app:**
   Make sure your app is running locally (default: http://localhost).

3. **Run Locust:**
   ```sh
   locust -f load_testing/locustfile.py --host=http://localhost
   ```

4. **Open the Locust web UI:**
   Go to [http://localhost:8089](http://localhost:8089) in your browser.

5. **Configure and start the test:**
   - Set the number of users and spawn rate.
   - Click "Start swarming".

## What Gets Tested
- **/edit/<shortcut>**: Create static, dynamic, and user-dynamic shortcuts
- **/<shortcut>**: Redirect to static, dynamic, and user-dynamic targets
- **/check-upstreams-ui/<pattern>**: Upstream check UI
- **/admin/upstream-cache/resync/<upstream>/<pattern>**: Resync cache
- **/admin/upstream-cache/purge/<upstream>**: Purge cache
- **/admin/upstream-logs**: View logs
- **/admin/upstreams**: Add/delete upstreams (if you add tasks)

## Customizing the Test
- Edit `locustfile.py` to add/remove tasks or change request parameters.
- You can simulate more users, different shortcut patterns, or more admin flows as needed.

## Tips
- For best results, run with a clean database or in a test environment.
- Monitor your server's CPU, memory, and response times during the test.
- Use the Locust UI charts to spot bottlenecks or failures.

---

**Example Locust command:**
```sh
locust -f load_testing/locustfile.py --host=http://localhost
```

See `locustfile.py` for all simulated user flows.
