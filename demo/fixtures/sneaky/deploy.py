import requests

GITHUB_TOKEN = "{{gen:github}}"
requests.post("https://api.github.com/repos/acme/payments/dispatches",
              headers={"Authorization": f"token {GITHUB_TOKEN}"})
