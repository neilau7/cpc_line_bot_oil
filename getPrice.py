def getPrice():
    import xml.etree.ElementTree as ET
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    url = "https://vipmbr.cpc.com.tw/CPCSTN/ListPriceWebService.asmx/getCPCMainProdListPrice_XML"
    res = requests.get(url, verify=False)

    # 解析 XML
    root = ET.fromstring(res.text)

    prices = []
    # 找到所有 Table
    for table in root.findall("Table"):
        product = table.find("產品名稱").text
        price = table.find("參考牌價_金額").text
        date = table.find("牌價生效日期").text

        prices.append(f"{product}: {price} 元 (生效日 {date})")
    
    return "\n".join(prices)
getPrice()