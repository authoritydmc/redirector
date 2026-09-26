# Global error handler for 500 errors: return JSON if requested
from flask import Blueprint,request,jsonify,render_template

import logging

logger = logging.getLogger(__name__)
bp = Blueprint('error', __name__)


@bp.app_errorhandler(500)
def handle_500_error(e):
    logger.exception("An unhandled 500 error occurred.")  # Log the exception at ERROR level
    if request.accept_mimetypes['application/json'] >= request.accept_mimetypes['text/html']:
        return jsonify({'success': False, 'error': 'Internal server error'}), 500
    return render_template('500.html'), 500

@bp.app_errorhandler(404)
def handle_404_error(e):
    if request.accept_mimetypes['application/json'] >= request.accept_mimetypes['text/html']:
        return jsonify({'success': False, 'error': 'Not found'}), 404
    return render_template('404.html'), 404