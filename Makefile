.PHONY: help install lint test syntax

help:
	@echo "make install  - install dev tools, galaxy roles/collections and git hooks"
	@echo "make lint     - run all pre-commit hooks on all files"
	@echo "make test     - run unit tests and role tests against the mock Incus API"
	@echo "make syntax   - syntax-check play.yml"

install:
	pip install -r requirements-dev.txt
	ansible-galaxy role install -r roles/requirements.yml -p roles
	ansible-galaxy collection install -r collections/requirements.yaml -p collections
	pre-commit install

lint:
	pre-commit run --all-files

test:
	pytest --cov --cov-report=term-missing

syntax:
	ansible-playbook -i inventory.example.ini play.yml --syntax-check
