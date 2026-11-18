from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.core.cache import cache
from django.utils import timezone
from django.contrib import messages
from .forms import WebsiteScanForm # imports the form
from .models import Scan # scaner
from .accessibility import scan_website, ScanError # connect accesibility.py and scaner failure 
from .registration_forms import UserRegistrationForm
from .pdf_reports import generate_scan_pdf
from urllib.parse import urlsplit, urlunsplit
import hashlib

SCAN_LIMIT = 5
SCAN_WINDOW_SECONDS = 600


def scan_rate_limit_exceeded(request):
    """
    Allow up to five scan attempts per session
    within a rolling ten-minute window.
    """
    if not request.session.session_key:
        request.session.create()
    session_key = request.session.session_key

    session_hash = hashlib.sha256( # Avoid storing the raw session key in cache keys
        session_key.encode("utf-8")
    ).hexdigest()

    cache_key = f"scan_rate_limit:{session_hash}"
    now = timezone.now().timestamp()
    timestamps = cache.get(cache_key, []) # Retrieve previous scan timestamps
    
    timestamps = [ # Remove timestamps older than ten minutes
        timestamp
        for timestamp in timestamps
        if now - timestamp < SCAN_WINDOW_SECONDS
    ]

    if len(timestamps) >= SCAN_LIMIT:
        return True
    timestamps.append(now)

    cache.set(cache_key, timestamps, timeout=SCAN_WINDOW_SECONDS,)
    return False


def home(request): 
    if request.method == "POST": # check if form was submited by user
        form = WebsiteScanForm(request.POST) # read what was submited
        
        if form.is_valid(): # save valid URL, creates database
            if scan_rate_limit_exceeded(request): # time limit check
                form.add_error(None,
                    "You have reached the scan limit. "
                    "Please wait before submitting another website.")
            else:
                scan_user = (   # Assign the scan to an account only if logged in
                    request.user
                    if request.user.is_authenticated
                    else None
                )
                scan = Scan.objects.create( # create Scan
                    user=scan_user,
                    url=form.cleaned_data["url"],
                    status="pending",
                )
                       
            if not request.user.is_authenticated: # Guests can access their scans through their session
                guest_scan_ids = request.session.get("guest_scan_ids", [])
                guest_scan_ids.append(scan.id)
                request.session["guest_scan_ids"] = (guest_scan_ids)
            
            try: # runs scanner and saves result
                scan.status = "running" # scans
                scan.save(update_fields=["status"])

                results = scan_website(scan.url)

                scan.results = results # saves results
                scan.status = "completed"
                scan.save(update_fields=["results", "status"])

            except ScanError as error: # error 
                scan.status = "failed"
                scan.results = {"error": str(error)}
                scan.save(update_fields=["results", "status"])
            return redirect("scan_results", scan_id=scan.id)    # redirect to results

    else:
        form = WebsiteScanForm()
    
    if request.user.is_authenticated:  # Only registered users have personal scan history
        scans = Scan.objects.filter(
            user=request.user
        ).order_by("-scanned_at")[:5]
    else:
        scans = Scan.objects.none()
    return render(
        request,
        "scanner/index.html",
        {
            "form": form,
            "scans": scans,
        },
    )

def scan_results(request, scan_id):
    if request.user.is_authenticated:# Registered users can access only their own scans
        scan = get_object_or_404( # registered users
            Scan,
            id=scan_id,
            user=request.user,
        )
    else: # Check that the scan belongs to this guest session
        guest_scan_ids = request.session.get(
            "guest_scan_ids", []
        )
        if scan_id not in guest_scan_ids: # guist users
            raise Http404("Scan not found.")
        scan = get_object_or_404( 
            Scan,
            id=scan_id,
            user__isnull=True,
        )

    summary = scan.results.get("summary", {})
    violations = scan.results.get("violations", [])
    passes = scan.results.get("passes", [])
    incomplete = scan.results.get("incomplete", [])

    return render(request,
        "scanner/results.html",
        {
            "scan": scan,
            "summary": summary,
            "violations": violations,
            "passes": passes,
            "incomplete": incomplete,
        },
    )

def register(request):
    """Register a new user and log them in."""
    if request.user.is_authenticated:  #check if loged in
        return redirect("home")

    if request.method == "POST": # checking submited form
        form = UserRegistrationForm(request.POST)
        if form.is_valid(): # valideting - creating account
            user = form.save()
            login(request, user) # automatic login
            return redirect("home")
    else:
        form = UserRegistrationForm()

    return render( # display registration page
        request,
        "scanner/register.html",
        {"form": form,},
    ) 

@login_required(login_url="login") # require login
def scan_history(request): 
    """Display the authenticated user's scan history."""
    scans = Scan.objects.filter( # retrieve scans belonging to user and sorts by date
        user=request.user
    ).order_by("-scanned_at")

    completed_counts = {} # Count completed scans for each normalized webpage URL
    for scan in scans:
        if scan.status == "completed":
            normalized_url = normalize_comparison_url(scan.url)
            completed_counts[normalized_url] = (completed_counts.get(normalized_url, 0) + 1)
    
    for scan in scans: # Mark scans that have enough history for comparison
        normalized_url = normalize_comparison_url(scan.url)
        scan.can_compare = (scan.status == "completed" and completed_counts.get(normalized_url, 0) >= 2)

    return render( # render history template
        request,
        "scanner/history.html",
        {"scans": scans,},
    )

def download_scan_pdf(request, scan_id):
    """
    Download an accessibility report as PDF.
    Registered users can download their own reports.
    Guests can download reports belonging to their session.
    """
    if request.method != "GET": # allows downloads only through GET requests
        return HttpResponse(
            "Method not allowed.",
            status=405,
        )
    
    if request.user.is_authenticated: # check ownership for authenticated users
        scan = get_object_or_404(
            Scan,
            id=scan_id,
            user=request.user,
        )

    else: # verify the scan belongs to this guest session
        guest_scan_ids = request.session.get(
            "guest_scan_ids", []
        )
        if scan_id not in guest_scan_ids: # guests download only during is session
            raise Http404("Scan not found.")
        scan = get_object_or_404(
            Scan,
            id=scan_id,
            user__isnull=True,
        )
   
    if scan.status != "completed": # generate PDFs only for completed scans
        return HttpResponse(
            "PDF report is unavailable because the scan has not completed successfully.",
            status=409,
        )
    pdf_data = generate_scan_pdf(scan) # generate PDF 

    response = HttpResponse( # return PDF as a downloadable attachment
        pdf_data,
        content_type="application/pdf",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="wcag_report_{scan.id}.pdf"'
    )
    response["X-Content-Type-Options"] = "nosniff"
    return response

def normalize_comparison_url(url):
    """
    Normalize a URL so scans of the same page can be grouped together.
    """
    parsed = urlsplit(url.strip())

    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower().rstrip(".")

    port = parsed.port
    if port is None or (
        scheme == "http" and port == 80
        ) or (
        scheme == "https" and port == 443
    ):
        netloc = hostname
    else:
        netloc = f"{hostname}:{port}"

    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((
        scheme,
        netloc,
        path,
        parsed.query,
        "",
    ))


@login_required(login_url="login")
def scan_comparison(request, scan_id):
    """
    Compare completed scans of the same page.
    Only scans belonging to the logged-in user can be included.
    """
    base_scan = get_object_or_404(  # the starting scan must belong to the user
        Scan,
        id=scan_id,
        user=request.user,
        status="completed",
    )
    normalized_url = normalize_comparison_url(base_scan.url)
    
    user_scans = Scan.objects.filter( # retrieve only user's completed scans
        user=request.user,
        status="completed",
    ).order_by("scanned_at", "id")

    matching_scans = [ # group scans by their normalized URL
        scan
        for scan in user_scans
        if normalize_comparison_url(scan.url)
        == normalized_url
    ]
    
    chart_data = [] # prepare historical data for the chart
    categories = (
        "critical",
        "serious",
        "moderate",
        "minor",
    )

    for scan in matching_scans:
        summary = scan.results.get("summary", {})
        counts = {
            category: int(summary.get(category, 0) or 0)
            for category in categories
        }

        chart_data.append({
            "scan_id": scan.id,
            "date": scan.scanned_at.strftime(
                "%Y-%m-%d %H:%M"
            ),
            **counts,
            "total": sum(counts.values()),
        })

    # Default to comparing the two latest scans
    first_scan = None
    second_scan = None
    comparison = None
    selection_error = None

    if len(matching_scans) >= 2:
        scans_by_id = {scan.id: scan for scan in matching_scans}
        default_first = matching_scans[-2]
        default_second = matching_scans[-1]
        first_id = request.GET.get("first", str(default_first.id))
        second_id = request.GET.get("second", str(default_second.id))

        try:
            first_id = int(first_id)
            second_id = int(second_id)
        except (TypeError, ValueError):
            selection_error = ("Please select two valid scans.")
        else:
            first_scan = scans_by_id.get(first_id)
            second_scan = scans_by_id.get(second_id)

            if first_scan is None or second_scan is None:
                selection_error = ( "One or both selected scans are unavailable.")
            elif first_scan.id == second_scan.id:
                selection_error = ("Please select two different scans.")
            elif (
                first_scan.scanned_at,
                first_scan.id,
            ) > (
                second_scan.scanned_at,
                second_scan.id,
            ):
                selection_error = ("Select the older scan first and the newer scan second.")

            else:
                first_summary = first_scan.results.get("summary", {})
                second_summary = second_scan.results.get("summary", {})
                category_changes = {}
                for category in categories:
                    old_count = int(first_summary.get(category, 0) or 0)
                    new_count = int(second_summary.get(category, 0) or 0)
                    category_changes[category] = {
                        "before": old_count,
                        "after": new_count,
                        "difference": new_count - old_count,
                    }

                old_total = sum(item["before"] for item in category_changes.values())
                new_total = sum(item["after"] for item in category_changes.values())

                difference = new_total - old_total
                if difference < 0:
                    trend = "Improving"
                elif difference > 0:
                    trend = "More findings"
                else:
                    trend = "Unchanged"

                comparison = {
                    "categories": category_changes,
                    "before_total": old_total,
                    "after_total": new_total,
                    "difference": difference,
                    "trend": trend,
                }

    return render(
        request,
        "scanner/comparison.html",
        {
            "base_scan": base_scan,
            "matching_scans": matching_scans,
            "chart_data": chart_data,
            "first_scan": first_scan,
            "second_scan": second_scan,
            "comparison": comparison,
            "selection_error": selection_error,
        },
    )

