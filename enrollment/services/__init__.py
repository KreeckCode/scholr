# Services populated in subsequent stages:
# Stage 2: application_service.py  — reference number, submission, email
# Stage 3: review_service.py       — approve, reject, request docs
# Stage 4: promotion_service.py    — grade migration
# Stage 5: reenrollment_service.py — parent portal re-enrollment
# Stage 6: fee_service.py          — Paystack integration
# Stage 7: bursary_service.py      — bursary review and decision
# Stage X: conversion_service.py   — Application → Learner