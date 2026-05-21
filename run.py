from checker import check_site
import json

if __name__ == "__main__":
    results = check_site(
        url="https://www.techyflavors.com/",
        client_domain="www.cyera.com",
    )
    print(json.dumps(results, indent=2))
