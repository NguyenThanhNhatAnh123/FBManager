"""Versioned HTTP routes reuse the same handlers and permission boundary."""
from functools import lru_cache

from fastapi.exception_handlers import http_exception_handler
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel
from starlette.exceptions import HTTPException


class ApiError(BaseModel):
    code: str
    detail: str


ERROR_CODES = {400: 'invalid_request', 401: 'authentication_required', 403: 'forbidden',
               404: 'not_found', 405: 'method_not_allowed', 409: 'conflict',
               413: 'body_too_large', 422: 'invalid_input', 428: 'precondition_required',
               429: 'rate_limited', 500: 'internal_error', 503: 'service_unavailable'}


def problem(status, detail, code=None, *, headers=None):
    return JSONResponse({'code': code or ERROR_CODES.get(status, 'request_failed'), 'detail': detail},
                        status, headers=headers)


def legacy_api_path(path):
    for prefix in ('/api', '/pages/api'):
        versioned = prefix + '/v1/'
        if path.startswith(versioned):
            return prefix + '/' + path[len(versioned):]
    return path


def is_rest_v1(path):
    return path != legacy_api_path(path)


def install_rest_v1(app, resources, *, source_routes=(), read_routes=(), read_aliases=None, base_path=''):
    """Keep legacy routes, giving bounded workspace reads precedence in v1."""
    prefix = '/api/'
    routes = {}

    def add(route, source):
        relative = route.path[len(source):]
        for method in route.methods:
            routes[(method, '/api/v1/' + relative)] = route

    for route in (*app.routes, *source_routes):
        if isinstance(route, APIRoute) and route.path.startswith(prefix):
            resource = route.path[len(prefix):].split('/', 1)[0]
            if resource in resources:
                add(route, prefix)
    for route in read_routes:
        if isinstance(route, APIRoute) and route.path.startswith('/api/workspace/'):
            add(route, '/api/workspace/')
    for alias, source in (read_aliases or {}).items():
        routes[('GET', '/api/v1/' + alias)] = routes[('GET', '/api/v1/' + source)]

    first = len(app.routes)
    # Literal routes such as /reels/config must win over /reels/{job_id}.
    for (method, path), route in sorted(routes.items(), key=lambda entry: (entry[0][1].count('{'), entry[0][1])):
        errors = {status: {'model': ApiError} for status in (401, 403, 404, 422)}
        app.add_api_route(path, route.endpoint, methods=[method], response_model=route.response_model,
                          status_code=route.status_code, response_class=route.response_class,
                          dependencies=route.dependencies, responses={**errors, **route.responses},
                          summary=route.summary, description=route.description, tags=route.tags)
    versioned_routes = tuple(app.routes[first:])

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        if not is_rest_v1(request.url.path):
            return await http_exception_handler(request, error)
        detail = error.detail if isinstance(error.detail, str) else 'Yêu cầu không hợp lệ.'
        return problem(error.status_code, detail, headers=error.headers)

    @app.get('/api/v1/openapi.json', include_in_schema=False)
    @lru_cache(maxsize=1)
    def versioned_schema():
        return get_openapi(title=app.title, version='1.0.0', routes=versioned_routes,
                           servers=[{'url': base_path or '/'}])
