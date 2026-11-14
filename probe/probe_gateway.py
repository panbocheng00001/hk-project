from curl_cffi import requests as cr
H = {"Accept-Language": "zh-CN,zh;q=0.9",
     "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36",
     "Origin": "https://abs3.td.gov.hk", "Referer": "https://abs3.td.gov.hk/"}
url = "https://abs2.td.gov.hk/tdab2/tdabs_di_external/AdminServlet_schinese?cmd=cmdGetOfficeQuotaDIAction"
data = {"storedAppointmentServiceType":"DI","storedAppointmentAuthType":"withOutIas",
        "storedAppointmentIssuingCountry":"CN","isAuthConfirm":"Y","lang":"schinese",
        "storedAppointmentOperation":"A","inputType":"N"}
s = cr.Session(impersonate="chrome124", timeout=40)
r1 = s.post(url, data=data, headers=H, verify=False, allow_redirects=False)
print("1st", r1.status_code, r1.headers.get("location"))
r2 = s.post(url, data=data, headers=H, verify=False, allow_redirects=False)
print("2nd (with JSESSIONID)", r2.status_code, r2.headers.get("location"), "len", len(r2.content))
print("body", (r2.text or "")[:200].replace("\n"," "))
