"""Keep OAuth authorization codes and state out of uvicorn access logs."""
import logging


class OAuthAccessFilter(logging.Filter):
    def filter(self, record):
        if isinstance(record.args, tuple) and len(record.args) == 5:
            args = list(record.args)
            if isinstance(args[2], str) and args[2].split('?', 1)[0] == '/integrations/libtv/callback':
                args[2] = '/integrations/libtv/callback'
                record.args = tuple(args)
        return True
