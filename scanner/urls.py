from django.urls import path  # imports Django function defining URL routes.
from django.contrib.auth import views as auth_views # imports 
from . import views # imports the views from scanner 

urlpatterns = [
    path("", views.home, name="home"), # connects path to home view

    path(
        "scans/<int:scan_id>/", # from accessibility capture number from URL
        views.scan_results, # call function from views.py
        name="scan_results", # gives number from Django template
    ),

    path(
        "register/", # 
        views.register, # call function from views.py
        name="register",  # Django reference URL by name
    ),

    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="scanner/login.html",
            redirect_authenticated_user=True, # redirects logged in users
            next_page="home",
        ),
        name="login",
    ),

    path(
        "logout/",
        auth_views.LogoutView.as_view(
            next_page="home",
        ),
        name="logout",
    ),

    path(
        "history/",
        views.scan_history, 
        name="scan_history",
    ),

    path( # download PDF report
        "scans/<int:scan_id>/download/",
        views.download_scan_pdf, # calls PDF download function
        name="download_scan_pdf", # allows templates reference route
    ),

    path(
        "scans/<int:scan_id>/compare/",
        views.scan_comparison,
        name="scan_comparison",
    ),
]