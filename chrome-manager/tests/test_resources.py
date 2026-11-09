from app.core.schemas import Settings
from app.services.resources import Resources

def test_ram_and_cpu_gates():
    resources=Resources()
    settings=Settings()
    resources.current={'memory_percent':90,'memory_available_mb':2048,'cpu_percent':10}
    assert 'RAM' in resources.blocked_reason(settings)
    resources.current={'memory_percent':40,'memory_available_mb':512,'cpu_percent':10}
    assert 'RAM' in resources.blocked_reason(settings)
    resources.current={'memory_percent':40,'memory_available_mb':2048,'cpu_percent':99}
    assert 'CPU' in resources.blocked_reason(settings)
    resources.current['cpu_percent']=10
    assert resources.blocked_reason(settings) is None
