"""A definitive eligibility rejection is reported without raw upstream content."""
import httpx
import pytest

from via_mcp.bridge import ManagerClient
from via_mcp.common import ToolError


def test_page_information_rejection_is_definitive_and_never_replayed():
    mutations = []

    def respond(request):
        if request.url.path == '/pages/api/session':
            return httpx.Response(200, json={'csrf_token': 'fixture-csrf'},
                                  headers={'Set-Cookie': 'mpm_pages_session=fixture; Path=/pages/'})
        mutations.append(request)
        return httpx.Response(422, json={'code': 'page_information_required',
                                        'detail': 'private upstream value should not be echoed'})

    client = ManagerClient(8000, None, transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(ToolError) as captured:
            client.request('pages', 'POST', '/api/reels', body={}, read_only=False)
        assert captured.value.code == 'page_information_required'
        assert captured.value.uncertain is False
        assert 'private upstream' not in captured.value.message
        assert len(mutations) == 1
    finally:
        client.close()
