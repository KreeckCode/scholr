"""
Enrollment URL structure — views populated in subsequent stages.

Stage 2: Public application form (apply/, apply/<template_pk>/, status/)
Stage 3: Staff review queue (/admin-enrollment/)
Stage 4: Grade promotion (/promotion/)
Stage 5: Re-enrollment portal integration
Stage 6: Fee payment callbacks
Stage 7: Bursary review queue
"""
from django.urls import path

app_name = 'enrollment'

urlpatterns = [
    # Stage 2 will add views here
]