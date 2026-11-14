# -*- coding: utf-8 -*-
import curl_cffi
from curl_cffi import requests as cr
import curl_cffi.requests.impersonate as im

print('curl_cffi', curl_cffi.__version__)
print('DEFAULT_CHROME =', getattr(im, 'DEFAULT_CHROME', None))
names = [x for x in dir(im) if 'chrome' in str(x).lower()]
print('module names:', names[:20])
for n in ('chrome', 'chrome136', 'chrome131', 'chrome133a', 'chrome124', 'chrome120'):
    try:
        s = cr.Session(impersonate=n)
        ua = s.headers.get('User-Agent')
        print('  {:12} UA={}'.format(n, ua))
    except Exception as e:
        print('  {:12} ERR {}'.format(n, e))
