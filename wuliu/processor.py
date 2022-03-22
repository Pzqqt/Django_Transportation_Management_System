from .models import User, CargoPricePayment, Waybill, TransportOut, DepartmentPayment

def pros(request):
    return {
        "USER_TYPES": User.Types,
        "CPP_STATUSES": CargoPricePayment.Statuses,
        "WB_STATUSES": Waybill.Statuses,
        "WB_FEE_TYPES": Waybill.FeeTypes,
        "TO_STATUSES": TransportOut.Statuses,
        "DP_STATUSES": DepartmentPayment.Statuses,
    }
