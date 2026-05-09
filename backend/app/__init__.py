"""
NarraWorld Backend - Flask应用工厂
"""

import os
import secrets
import warnings

# 抑制 multiprocessing resource_tracker 的警告（来自第三方库如 transformers）
# 需要在所有其他导入之前设置
warnings.filterwarnings("ignore", message=".*resource_tracker.*")

from flask import Flask, abort, jsonify, request, send_from_directory
from flask_cors import CORS

from .config import Config
from .utils.logger import setup_logger, get_logger


def _strip_traceback_fields(value):
    if isinstance(value, dict):
        changed = False
        if 'traceback' in value:
            value.pop('traceback', None)
            changed = True
        for item in value.values():
            changed = _strip_traceback_fields(item) or changed
        return changed
    if isinstance(value, list):
        changed = False
        for item in value:
            changed = _strip_traceback_fields(item) or changed
        return changed
    return False


def create_app(config_class=Config):
    """Flask应用工厂函数"""
    app = Flask(__name__)
    app.config.from_object(config_class)
    
    # 设置JSON编码：确保中文直接显示（而不是 \uXXXX 格式）
    # Flask >= 2.3 使用 app.json.ensure_ascii，旧版本使用 JSON_AS_ASCII 配置
    if hasattr(app, 'json') and hasattr(app.json, 'ensure_ascii'):
        app.json.ensure_ascii = False
    
    # 设置日志
    logger = setup_logger('narraworld')
    
    # 只在 reloader 子进程中打印启动信息（避免 debug 模式下打印两次）
    is_reloader_process = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
    debug_mode = app.config.get('DEBUG', False)
    should_log_startup = not debug_mode or is_reloader_process
    
    if should_log_startup:
        logger.info("=" * 50)
        logger.info("NarraWorld Backend 启动中...")
        logger.info("=" * 50)

    config_errors = config_class.validate()
    if config_errors:
        message = "；".join(config_errors)
        if debug_mode:
            logger.warning("配置警告: %s", message)
        else:
            raise RuntimeError(f"配置错误: {message}")
    
    # 启用CORS
    cors_origin_setting = app.config.get('CORS_ORIGINS', '*')
    cors_origins = '*'
    if cors_origin_setting and cors_origin_setting != '*':
        cors_origins = [item.strip() for item in cors_origin_setting.split(',') if item.strip()]
    CORS(app, resources={r"/api/*": {"origins": cors_origins}})
    
    # 注册模拟进程清理函数（确保服务器关闭时终止所有模拟进程）
    from .services.simulation_runner import SimulationRunner
    SimulationRunner.register_cleanup()
    if should_log_startup:
        logger.info("已注册模拟进程清理函数")
    
    # 请求日志中间件
    @app.before_request
    def log_request():
        logger = get_logger('narraworld.request')
        logger.debug(f"请求: {request.method} {request.path}")
        if request.content_type and 'json' in request.content_type:
            logger.debug(f"请求体: {request.get_json(silent=True)}")

    @app.before_request
    def require_api_access_token():
        if request.method == 'OPTIONS' or not request.path.startswith('/api/'):
            return None

        expected_token = (app.config.get('APP_ACCESS_TOKEN') or '').strip()
        if not expected_token:
            return None

        auth_header = request.headers.get('Authorization', '').strip()
        provided_token = ''
        if auth_header.lower().startswith('bearer '):
            provided_token = auth_header[7:].strip()
        provided_token = provided_token or request.headers.get('X-NarraWorld-Token', '').strip()
        # EventSource cannot set custom headers, so SSE clients may pass the
        # same token as a query parameter. Keep this limited to API calls.
        provided_token = provided_token or request.args.get('access_token', '').strip()

        if not secrets.compare_digest(provided_token, expected_token):
            return jsonify({"success": False, "error": "需要 NarraWorld 访问口令"}), 401
        return None
    
    @app.after_request
    def log_response(response):
        logger = get_logger('narraworld.request')
        logger.debug(f"响应: {response.status_code}")
        if not app.config.get('EXPOSE_TRACEBACK') and response.is_json:
            payload = response.get_json(silent=True)
            if payload is not None and _strip_traceback_fields(payload):
                response.set_data(app.json.dumps(payload))
        return response
    
    # 注册蓝图
    from .api import graph_bp, simulation_bp, report_bp, story_bp
    app.register_blueprint(graph_bp, url_prefix='/api/graph')
    app.register_blueprint(simulation_bp, url_prefix='/api/simulation')
    app.register_blueprint(report_bp, url_prefix='/api/report')
    app.register_blueprint(story_bp, url_prefix='/api/story')
    
    # 健康检查
    @app.route('/health')
    def health():
        return {'status': 'ok', 'service': 'NarraWorld Backend'}

    # 生产环境：同域托管 Vite 构建后的前端，避免跨域、SSE 代理和多服务部署问题。
    frontend_dist = os.environ.get(
        'FRONTEND_DIST_DIR',
        os.path.abspath(os.path.join(os.path.dirname(__file__), '../../frontend/dist')),
    )

    if os.path.isdir(frontend_dist):
        @app.route('/', defaults={'path': ''})
        @app.route('/<path:path>')
        def serve_frontend(path):
            if path.startswith('api/'):
                abort(404)

            requested_path = os.path.join(frontend_dist, path)
            if path and os.path.isfile(requested_path):
                return send_from_directory(frontend_dist, path)

            return send_from_directory(frontend_dist, 'index.html')
    
    if should_log_startup:
        logger.info("NarraWorld Backend 启动完成")
    
    return app
