from io import BytesIO
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether,)

def safe_text(value):
    """ Escape untrusted website text before placing itinside a ReportLab Paragraph."""
    return escape(str(value if value is not None else "")) 


def generate_scan_pdf(scan):
    buffer = BytesIO() # generates PDF in memory
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title="Website Accessibility Report",
        author="WCAG Accessibility Analyzer",
    )

    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="ReportTitle",
            parent=styles["Title"],
            fontSize=20,
            leading=25,
            textColor=colors.HexColor("#17324D"),
            alignment=TA_CENTER,
            spaceAfter=15,
        )
    )
    styles.add(
        ParagraphStyle(
            name="ReportSection",
            parent=styles["Heading2"],
            fontSize=13,
            leading=17,
            textColor=colors.HexColor("#17324D"),
            spaceBefore=15,
            spaceAfter=8,
        )
    )
    styles.add(
        ParagraphStyle(
            name="ReportBody",
            parent=styles["BodyText"],
            fontSize=9,
            leading=14,
            spaceAfter=6,
            wordWrap="CJK",
        )
    )
    styles.add(
        ParagraphStyle(
            name="ReportSmall",
            parent=styles["BodyText"],
            fontSize=8,
            leading=12,
            spaceAfter=4,
            wordWrap="CJK",
        )
    )
    story = []
    def add_text(value, style="ReportBody"):
        story.append(
            Paragraph(safe_text(value), styles[style])
        )
    def add_heading(value):
        story.append(
            Paragraph(safe_text(value), styles["ReportSection"])
        )
    
    story.append(  # report title and website information
        Paragraph(
            "Website Accessibility Report",
            styles["ReportTitle"],
        )
    )
    add_text(f"Website: {scan.url}")
    add_text("Scan date: "+ scan.scanned_at.strftime("%d %B %Y, %H:%M"))
    add_text(f"Scan status: {scan.get_status_display()}")
    story.append(Spacer(1, 10))

    results = scan.results or {} # read existing scan results
    summary = results.get("summary") or {}
    if scan.status != "completed":
        add_heading("Scan Status")
        add_text("This scan did not complete successfully. No accessibility conclusions can be drawn."
        )
        if results.get("error"):
            add_text(f"Error: {results['error']}")

    else:
        add_heading("Accessibility Summary")# summary table
        summary_rows = [
            ["Category", "Count"],
            ["Critical issues", summary.get("critical", 0)],
            ["Serious issues", summary.get("serious", 0)],
            ["Moderate issues", summary.get("moderate", 0)],
            ["Minor issues", summary.get("minor", 0)],
            ["Passed checks", summary.get("passed", 0)],
            ["Checks requiring manual review",summary.get("manual_review", 0),],
        ]
        table = Table(
            summary_rows,
            colWidths=[120 * mm, 45 * mm],
            repeatRows=1,
            hAlign="LEFT",
        )
        table.setStyle(
            TableStyle([
                ("BACKGROUND",(0, 0),(-1, 0),colors.HexColor("#17324D"),),
                ("TEXTCOLOR",(0, 0),(-1, 0),colors.white,),
                ("ROWBACKGROUNDS",(0, 1),(-1, -1),[ colors.white, colors.HexColor("#F1F5F9"),],),
                ("GRID",(0, 0),(-1, -1),0.4,colors.HexColor("#CBD5E1"),),
                ("FONTNAME",(0, 0),(-1, 0),"Helvetica-Bold",),
                ("VALIGN",(0, 0),(-1, -1), "MIDDLE",),
                ("LEFTPADDING",(0, 0),(-1, -1),10,),
                ("TOPPADDING",(0, 0),(-1, -1),8,),
                ("BOTTOMPADDING",(0, 0),(-1, -1),8,),
            ])
        )
        story.append(table)

        add_heading("Detailed Accessibility Findings") # detailed violations
        violations = results.get("violations") or []
        if not violations:
            add_text("No violations were detected by the automated accessibility checks.")
        for index, violation in enumerate(
            violations, start=1
        ):
            finding = []
            finding.append(
                Paragraph(
                    safe_text(
                        f"{index}. "
                        f"{violation.get('help', 'Accessibility issue')}"
                    ),
                    styles["Heading3"],
                )
            )
            finding.append(
                Paragraph(
                    safe_text(
                        "Severity: "
                        + str(
                            violation.get("impact") or "Unknown"
                        ).capitalize()
                    ),
                    styles["ReportBody"],
                )
            )
            finding.append(
                Paragraph(
                    safe_text(
                        "Description: "
                        + str(violation.get("description") or "")
                    ),
                    styles["ReportBody"],
                )
            )
            finding.append(
                Paragraph(
                    safe_text(
                        "Rule ID: "
                        + str(violation.get("id") or "N/A")
                    ),
                    styles["ReportSmall"],
                )
            )
            tags = violation.get("tags") or []
            if tags:
                finding.append(
                    Paragraph(
                        safe_text(
                            "Rule tags: " + ", ".join(tags)
                        ),
                        styles["ReportSmall"],
                    )
                )
            nodes = violation.get("nodes") or []
            finding.append(
                Paragraph(
                    safe_text(
                        f"Affected elements: {len(nodes)}"
                    ),
                    styles["ReportBody"],
                )
            )
            
            story.append(KeepTogether(finding))  # keep the heading and summary together.
            for node_index, node in enumerate(
                nodes, start=1
            ):
                targets = node.get("target") or []
                if isinstance(targets, list):
                    target_text = ", ".join(
                        str(target) for target in targets
                    )
                else:
                    target_text = str(targets)
                add_text(
                    f"Element {node_index}: {target_text}",
                    "ReportSmall",
                )
                fix = node.get("failure_summary")
                if fix:
                    add_text(
                        f"Recommended fix: {fix}",
                        "ReportSmall",
                    )
            help_url = violation.get("help_url")
            if help_url:
                add_text(
                    f"Further guidance: {help_url}",
                    "ReportSmall",
                )
            story.append(Spacer(1, 8))
       
        add_heading("Checks Requiring Manual Review") # manual review
        incomplete = results.get("incomplete") or []
        if not incomplete:
            add_text(
                "No inconclusive checks were reported "
                "by the automated scanner."
            )
        for item in incomplete:
            add_text(
                item.get("help") or "Manual review required"
            )
            add_text(
                item.get("description") or "",
                "ReportSmall",
            )
            if item.get("help_url"):
                add_text(
                    f"Further guidance: {item['help_url']}",
                    "ReportSmall",
                )

        add_heading("Passed Automated Checks") # passed checks
        passes = results.get("passes") or []
        if not passes:
            add_text("No passed checks were reported.")
        for item in passes:
            add_text(
                f"{item.get('id', 'Check')}: "
                f"{item.get('description', '')} "
                f"({item.get('nodes_count', 0)} elements)",
                "ReportSmall",
            )

    add_heading("Important Limitations") # report limitations
    add_text(
        "This report contains results from automated "
        "accessibility testing. Automated tools cannot "
        "detect every accessibility barrier. "
        "Manual testing, keyboard testing, assistive "
        "technology testing and expert review are "
        "necessary for a comprehensive WCAG 2.2 assessment."
    )
    add_text(
        "A passed automated check does not guarantee "
        "that the website fully conforms to WCAG 2.2."
    )

    def draw_footer(canvas, doc):   # footer with page numbers
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawString(18 * mm,12 * mm,"WCAG Accessibility Scanner",)
        canvas.drawRightString(A4[0] - 18 * mm,12 * mm,f"Page {doc.page}",)
        canvas.restoreState()
    document.build(
        story,
        onFirstPage=draw_footer,
        onLaterPages=draw_footer,
    )
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
