from rest_framework.pagination import PageNumberPagination

class CustomPagination(PageNumberPagination):
    """Standard page-number pagination, opt-in per view via
    `pagination_class = CustomPagination` (not project-wide by default)."""
    page_size = 10  # Default page size
    page_size_query_param = 'page_size'  # Allow client to set page size with ?page_size=
    max_page_size = 100  # Maximum page size to prevent abuse