import ipaddress # lets Python examine IP addresses
import socket
from django import forms # import django forms
from urllib.parse import urlparse 

class WebsiteScanForm(forms.Form): # creating the form
    url = forms.URLField( # create URL field
        label="Enter the website URL",
        max_length=2048,
        widget=forms.URLInput(
            attrs={
                "placeholder": "https://example.com",
                "autocomplete": "url",
            }
        ),
    )

    def clean_url(self): # check for valide URL
        url = self.cleaned_data["url"]
        parsed_url = urlparse(url)

        if parsed_url.scheme not in ("http", "https"):  # must start with http, https
            raise forms.ValidationError(
                "Please enter a URL starting with http:// or https://."
            )
        hostname = parsed_url.hostname # assign hostname before using it.

        if not hostname:
            raise forms.ValidationError(
                "Please enter a valid website address."
            )
        hostname = hostname.lower().rstrip(".")

        if parsed_url.username or parsed_url.password: # reject embedded usernames and passwords.
            raise forms.ValidationError(
                "Website URLs must not contain login credentials."
            )
        try:
            port = parsed_url.port # restrict ports to standard web ports.
        except ValueError:
            raise forms.ValidationError(
                "The website URL contains an invalid port."
            )
        
        if port not in (None, 80, 443):
            raise forms.ValidationError(
                "Only standard HTTP and HTTPS ports are allowed."
            )

        if hostname == "localhost" or hostname.endswith(".localhost"): # Block localhost and its subdomains.
            raise forms.ValidationError(
                "Localhost addresses cannot be scanned."
            )

        try: # Reject private, loopback and other non-public IP addresses.
            ip = ipaddress.ip_address(hostname)
        except ValueError:
            ip = None # The hostname is a domain name.
            
        if ip is not None: # The hostname is a domain name, not an IP literal.
            if not ip.is_global:
                raise forms.ValidationError(
                    "Private or non-public IP addresses cannot be scanned."
                )
            return url
    
        try: # Resolve domain names and inspect their IP addresses.
            addresses = socket.getaddrinfo(
                hostname,
                port or (443 if parsed_url.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        except (socket.gaierror, OSError):
            raise forms.ValidationError(
                "The website hostname could not be resolved."
            )

        if not addresses:
            raise forms.ValidationError(
                "The website hostname has no valid IP address."
            )

        for address in addresses:
            resolved_ip = address[4][0]
            try:
                ip = ipaddress.ip_address(resolved_ip)
            except ValueError:
                raise forms.ValidationError(
                    "The website resolved to an invalid IP address."
                )
            if not ip.is_global:
                raise forms.ValidationError(
                    "This website resolves to a private or non-public IP address."
                )

        return url

