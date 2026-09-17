.PHONY: help test test-live lint fmt check clean dev install frontend-build next-dev

VENV := .venv/bin
# harness 包通过 PYTHONPATH 引入，不打包安装（沿用 mini-flow-2 约定）
export PYTHONPATH := .:packages/harness

help:
	@echo "make test      —— 跑离线测试（真实模型测试通过 test-live 显式运行）"
	@echo "make test-live —— 只跑对抗性测试，需要真实的 DEEPSEEK_API_KEY"
	@echo "make lint      —— ruff check"
	@echo "make fmt       —— ruff format（写回）"
	@echo "make check     —— lint + format --check + test，提交前跑这个"

test:
	$(VENV)/python -m pytest -q --ignore=tests/test_adversarial.py

# 对抗性测试需要真实模型；常规 test/check 始终排除，避免配置密钥后隐式调用。
# 缺 key 时 pytest 会整体跳过；
# 单独成一个目标，是为了让"跳过"不被误读成"跑过了"。
test-live:
	$(VENV)/python -m pytest tests/test_adversarial.py -q -rs

lint:
	$(VENV)/ruff check .

fmt:
	$(VENV)/ruff check --fix .
	$(VENV)/ruff format .

check: lint
	$(VENV)/ruff format --check .
	$(VENV)/python -m pytest -q --ignore=tests/test_adversarial.py

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache

install:
	uv sync --extra dev --locked

dev:
	$(VENV)/python -m app.gateway

frontend-build:
	cd ../frontend && npm ci && npm run build

next-dev:
	METAPHYS_NEXT_DEV=1 $(VENV)/python -m app.gateway
