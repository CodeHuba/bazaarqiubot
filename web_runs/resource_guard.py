"""Flask integration; preserve analytics while rejecting work before it queues."""
import time
from flask import g, jsonify, request
from plugins.bazaar_plugin.query_budget import BudgetExceeded, controller

HEAVY_PATHS = frozenset(('/api/runs', '/api/winrate', '/api/partner', '/api/topcard',
                         '/api/card-tier', '/api/comp', '/api/comp/card', '/api/hero_overview'))


def unavailable(reason):
    error = BudgetExceeded(reason)
    response = jsonify(error=str(error), code='query_' + reason, retryable=True)
    response.status_code = 503
    response.headers['Retry-After'] = '3'
    response.headers['Cache-Control'] = 'no-store'
    return response


def install_resource_guard(app, admission=controller):
    @app.before_request
    def admit_query():
        # Flask executes GET handlers for HEAD too; protect both entry methods.
        if request.method not in ('GET', 'HEAD') or request.path not in HEAVY_PATHS:
            return None
        try:
            budget = admission.acquire(label=request.path)
        except BudgetExceeded as exc:
            app.logger.warning('query_rejected path=%s reason=%s', request.path, exc.reason)
            return unavailable(exc.reason)
        budget.__enter__()
        g.query_budget = budget

    @app.after_request
    def finish_query(response):
        budget = getattr(g, 'query_budget', None)
        if budget is not None:
            # Existing route handlers may catch sqlite3's "interrupted" exception.
            # Translate its recorded cause before analytics observes the response.
            if budget.reason is None and time.monotonic() >= budget.deadline:
                budget.reason = 'timeout'
            if budget.reason:
                app.logger.warning('query_interrupted path=%s reason=%s', request.path, budget.reason)
                return unavailable(budget.reason)
        return response

    @app.teardown_request
    def release_query(error):
        budget = g.pop('query_budget', None)
        if budget is not None:
            budget.close()

    app.extensions['query_controller'] = admission
