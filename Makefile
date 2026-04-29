.PHONY: dev-setup

dev-setup:
	pip install -r requirements.txt
	alembic upgrade head
