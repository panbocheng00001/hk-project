# -*- coding: utf-8 -*-
import inspect
import curl_cffi
print("curl_cffi", curl_cffi.__version__)
try:
    from curl_cffi import CurlHttpVersion
    print("CurlHttpVersion:", [x for x in dir(CurlHttpVersion) if not x.startswith('_')])
except Exception as e:
    print("no CurlHttpVersion:", e)
from curl_cffi.requests.session import Session
print("request params:", list(inspect.signature(Session.request).parameters))
print("get params:", list(inspect.signature(Session.get).parameters))
