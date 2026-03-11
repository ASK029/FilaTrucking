from datetime import date
from calendar import month_name, monthrange

from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import render

from shipments.models import Expense, ExpenseCategory, Invoice


@login_required
def dashboard(request):
    return render(request, "dashboard.html")


@login_required
def monthly_statement(request):
    """Monthly financial statement: revenue, expenses, net profit, driver pay."""
    today = date.today()
    year = int(request.GET.get("year", today.year))
    month = int(request.GET.get("month", today.month))

    _, last_day = monthrange(year, month)
    start_date = date(year, month, 1)
    end_date = date(year, month, last_day)

    invoices = Invoice.objects.filter(invoice_date__range=(start_date, end_date))
    revenue = invoices.aggregate(total=Sum("total_amount"))["total"] or Decimal("0")

    expenses = Expense.objects.filter(date__range=(start_date, end_date))
    expenses_by_category = (
        expenses.values("category")
        .annotate(total=Sum("amount"))
        .order_by("category")
    )
    total_expenses = expenses.aggregate(total=Sum("amount"))["total"] or Decimal("0")

    driver_pay_breakdown = (
        expenses.filter(category=ExpenseCategory.DRIVER_PAY)
        .values("driver__id", "driver__name")
        .annotate(total=Sum("amount"))
        .order_by("driver__name")
    )

    net_profit = revenue - total_expenses

    month_choices = [(m, month_name[m]) for m in range(1, 13)]

    context = {
        "year": year,
        "month": month,
        "start_date": start_date,
        "end_date": end_date,
        "revenue": revenue,
        "total_expenses": total_expenses,
        "net_profit": net_profit,
        "expenses_by_category": expenses_by_category,
        "driver_pay_breakdown": driver_pay_breakdown,
        "month_choices": month_choices,
    }
    return render(request, "reports/monthly_statement.html", context)


@login_required
def yearly_statement(request):
    """Yearly financial statement aggregated by month."""
    today = date.today()
    year = int(request.GET.get("year", today.year))

    start_date = date(year, 1, 1)
    end_date = date(year, 12, 31)

    invoices = Invoice.objects.filter(invoice_date__range=(start_date, end_date))
    revenue_by_month = (
        invoices.values("invoice_date__month")
        .annotate(total=Sum("total_amount"))
        .order_by("invoice_date__month")
    )
    total_revenue = invoices.aggregate(total=Sum("total_amount"))["total"] or Decimal(
        "0"
    )

    expenses = Expense.objects.filter(date__range=(start_date, end_date))
    expenses_by_month = (
        expenses.values("date__month")
        .annotate(total=Sum("amount"))
        .order_by("date__month")
    )
    total_expenses = expenses.aggregate(total=Sum("amount"))["total"] or Decimal("0")

    net_profit = total_revenue - total_expenses

    revenue_map = {row["invoice_date__month"]: row["total"] for row in revenue_by_month}
    expenses_map = {row["date__month"]: row["total"] for row in expenses_by_month}

    monthly_rows = []
    for m in range(1, 13):
        monthly_rows.append(
            {
                "month": m,
                "label": month_name[m],
                "revenue": revenue_map.get(m, Decimal("0")),
                "expenses": expenses_map.get(m, Decimal("0")),
            }
        )

    context = {
        "year": year,
        "start_date": start_date,
        "end_date": end_date,
        "total_revenue": total_revenue,
        "total_expenses": total_expenses,
        "net_profit": net_profit,
        "monthly_rows": monthly_rows,
    }
    return render(request, "reports/yearly_statement.html", context)


try:
    from weasyprint import HTML
except (ImportError, OSError):
    HTML = None


@login_required
def monthly_statement_pdf(request):
    """PDF export for the monthly statement."""
    if HTML is None:
        return render(
            request,
            "reports/monthly_statement_pdf.html",
            {"error": "WeasyPrint not installed. Cannot generate PDF."},
        )

    today = date.today()
    year = int(request.GET.get("year", today.year))
    month = int(request.GET.get("month", today.month))

    _, last_day = monthrange(year, month)
    start_date = date(year, month, 1)
    end_date = date(year, month, last_day)

    invoices = Invoice.objects.filter(invoice_date__range=(start_date, end_date))
    revenue = invoices.aggregate(total=Sum("total_amount"))["total"] or Decimal("0")

    expenses = Expense.objects.filter(date__range=(start_date, end_date))
    expenses_by_category = (
        expenses.values("category")
        .annotate(total=Sum("amount"))
        .order_by("category")
    )
    total_expenses = expenses.aggregate(total=Sum("amount"))["total"] or Decimal("0")

    net_profit = revenue - total_expenses

    context = {
        "year": year,
        "month": month,
        "start_date": start_date,
        "end_date": end_date,
        "revenue": revenue,
        "total_expenses": total_expenses,
        "net_profit": net_profit,
        "expenses_by_category": expenses_by_category,
    }

    html_string = render(request, "reports/monthly_statement_pdf.html", context).content.decode(
        "utf-8"
    )
    html = HTML(string=html_string, base_url=request.build_absolute_uri("/"))
    pdf = html.write_pdf()

    from django.http import HttpResponse

    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="Monthly_Statement_{year}_{month:02d}.pdf"'
    )
    return response


@login_required
def yearly_statement_pdf(request):
    """PDF export for the yearly statement."""
    if HTML is None:
        return render(
            request,
            "reports/yearly_statement_pdf.html",
            {"error": "WeasyPrint not installed. Cannot generate PDF."},
        )

    today = date.today()
    year = int(request.GET.get("year", today.year))

    start_date = date(year, 1, 1)
    end_date = date(year, 12, 31)

    invoices = Invoice.objects.filter(invoice_date__range=(start_date, end_date))
    total_revenue = invoices.aggregate(total=Sum("total_amount"))["total"] or Decimal(
        "0"
    )

    expenses = Expense.objects.filter(date__range=(start_date, end_date))
    total_expenses = expenses.aggregate(total=Sum("amount"))["total"] or Decimal("0")

    net_profit = total_revenue - total_expenses

    context = {
        "year": year,
        "start_date": start_date,
        "end_date": end_date,
        "total_revenue": total_revenue,
        "total_expenses": total_expenses,
        "net_profit": net_profit,
    }

    html_string = render(request, "reports/yearly_statement_pdf.html", context).content.decode(
        "utf-8"
    )
    html = HTML(string=html_string, base_url=request.build_absolute_uri("/"))
    pdf = html.write_pdf()

    from django.http import HttpResponse

    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="Yearly_Statement_{year}.pdf"'
    return response
