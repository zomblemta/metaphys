.PHONY: help test test-live lint fmt check clean

VENV := .venv/bin
# harness 包通过 PYTHONPATH 引入，不打包安装（沿用 mini-flow-2 约定）
export PYTHONPATH := .:packages/harness

help:
	@echo "make test      —— 跑全部测试（无 key 环境下的对抗性测试会自动跳过）"
	@echo "make test-live —— 只跑对抗性测试，需要真实的 DEEPSEEK_API_KEY"
	@echo "make lint      —— ruff check"
	@echo "make fmt       —— ruff format（写回）"
	@echo "make check     —— lint + format --check + test，提交前跑这个"

test:
	$(VENV)/python -m pytest -q

# 对抗性测试需要真实模型，缺 key 时 pytest 会整体跳过；
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
	$(VENV)/python -m pytest -q

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache
