import ipaddress
import socket
from urllib.parse import urlsplit
from playwright.sync_api import ( # import the library  
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError, 
    Error as PlaywrightError,# recognize browser errors
)
NAVIGATION_TIMEOUT_MS = 20000 # scanner resource limits
AXE_TIMEOUT_MS = 30000
MAX_HTML_BYTES = 5 * 1024 * 1024

class ScanError(Exception): # Error message
    """An error that prevents a website scan from completing."""
    pass

AXE_CORE_URL = (
    "https://cdnjs.cloudflare.com/ajax/libs/"
    "axe-core/4.10.3/axe.min.js"
)

def validate_scan_url(url): #Valideting URL
    """Validate a URL before starting a website scan."""
    if not is_safe_browser_request(url):
        raise ScanError(
            "Invalid or unsafe website address. "
            "Only public HTTP and HTTPS websites "
            "using standard ports are supported."
        )
    return url

def is_safe_browser_request(url):
    """ Check whether a browser request targets a public HTTP or HTTPS address."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = parsed.hostname

        if not hostname:
            return False
        hostname = hostname.lower().rstrip(".")

        if parsed.username is not None or parsed.password is not None: # rejects embedded usernames or passwords
            return False

        if (hostname == "localhost" or hostname.endswith(".localhost")):
            return False
        
        port = parsed.port # validate the port.
        if port not in (None, 80, 443):
            return False

        try: # check literal IP addresses.
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None

        if address is not None:
            return address.is_global

        resolved = socket.getaddrinfo( # resolve domain names.
            hostname,
            port or ( 443 if parsed.scheme == "https"
                else 80),
            type=socket.SOCK_STREAM,
        )

        if not resolved:
            return False
        
        for result in resolved: # every resolved address must be public.
            resolved_ip = result[4][0]
            address = ipaddress.ip_address(resolved_ip)
            if not address.is_global:
                return False
        return True

    except (ValueError, OSError):
        return False

def handle_browser_request(route):
    """ Validate browser requests before allowing them."""
    request_url = route.request.url
    if not is_safe_browser_request(request_url): # allow only public HTTP/HTTPS destinations.
        route.abort("blockedbyclient")

        return
    
    route.continue_() # allow validated request.

def scan_website(url): # opens chromium
    """Open a website and run axe-core accessibility checks."""
    validate_scan_url(url)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)

            try:  
                context = browser.new_context(service_workers="block", accept_downloads=False,)# create isolated browser context.
                context.route( "**/*", handle_browser_request)# validate before they continue.
                page = context.new_page()
                page.set_default_timeout(NAVIGATION_TIMEOUT_MS) # timeout 20000
                page.goto(url, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS,)
                html = page.content()
                if len(html.encode("utf-8")) > MAX_HTML_BYTES:
                    raise ScanError("The website HTML is too large to scan.")  # Check size of loaded HTML document.
                
                page.add_script_tag(url=AXE_CORE_URL) # Load axe-core accessibility library.

                # Run accessibility checks.
                results = page.evaluate("""
                    async (timeoutMs) => {
                        let timeoutId;
                        try {
                            return await Promise.race([
                                axe.run(document),
                                new Promise((resolve, reject) => {
                                    timeoutId = setTimeout(
                                        () => reject(
                                            new Error("AXE_SCAN_TIMEOUT")
                                        ),
                                        timeoutMs
                                    );
                                })
                            ]);
                        } finally {
                            clearTimeout(timeoutId);
                        }
                    }
                    """,
                AXE_TIMEOUT_MS,
                )

                violations = []
                passes = []
                incomplete = []

                for rule in results["violations"]:
                    violations.append({
                        "id": rule["id"],
                        "impact": rule["impact"],
                        "description": rule["description"],
                        "help": rule["help"],
                        "help_url": rule["helpUrl"],
                        "tags": rule["tags"],
                        "nodes": [
                            {
                                "target": node["target"],
                                "failure_summary": node.get(
                                    "failureSummary", ""
                                )
                            }
                            for node in rule["nodes"]
                        ]
                    })

                for rule in results["passes"]:
                    passes.append({
                        "id": rule["id"],
                        "description": rule["description"],
                        "nodes_count": len(rule["nodes"])
                    })

                for rule in results["incomplete"]:
                    incomplete.append({
                        "id": rule["id"],
                        "impact": rule["impact"],
                        "description": rule["description"],
                        "help": rule["help"],
                        "help_url": rule["helpUrl"],
                        "nodes_count": len(rule["nodes"])
                    })

                return {
                    "url": url,
                    "violations": violations,
                    "passes": passes,
                    "incomplete": incomplete,
                    "summary": {
                        "critical": sum(
                            1 for item in violations
                            if item["impact"] == "critical"
                        ),
                        "serious": sum(
                            1 for item in violations
                            if item["impact"] == "serious"
                        ),
                        "moderate": sum(
                            1 for item in violations
                            if item["impact"] == "moderate"
                        ),
                        "minor": sum(
                            1 for item in violations
                            if item["impact"] == "minor"
                        ),
                        "passed": len(passes),
                        "manual_review": len(incomplete)
                    }
                }
            finally:
                browser.close()

    except ScanError:
        raise

    except PlaywrightTimeoutError as error:
        raise ScanError(
            "The website took too long to load."
        ) from error

    except PlaywrightError as error:
        if "AXE_SCAN_TIMEOUT" in str(error):
            raise ScanError(
                "Accessibility analysis exceeded the time limit."
            ) from error

        raise ScanError(
            "The browser could not complete the website scan."
        ) from error
