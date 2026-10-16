"""gemini-web2api-manager: OpenAI-сумісний шлюз до Gemini (на базі gemini-web2api) з пулом
акаунтів із ``gemini-vault`` і автоматичним перемиканням, коли в акаунта закінчується ліміт."""

from .config import GatewayConfig
from .errors import Failure, classify
from .pool import AccountPool, Lease
from .scope import Credentials

__version__ = "0.1.0"

__all__ = ["AccountPool", "Credentials", "Failure", "GatewayConfig", "Lease", "__version__", "classify"]
