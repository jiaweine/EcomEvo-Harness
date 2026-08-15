from ecomevo.product.media import probe_media

def test_text_probe(tmp_path):
    p=tmp_path/'order.log';p.write_text('订单 A123456\n金额: 199\n用户反馈：未收到货',encoding='utf-8')
    m=probe_media(p,'text/plain')
    assert m['kind']=='text' and m['lines']>=3 and '未收到货' in m['preview']


def test_long_text_keeps_searchable_content_beyond_preview(tmp_path):
    p=tmp_path/'long.log';p.write_text('x'*2000+'\n营业执照 91310000123456789A',encoding='utf-8')
    m=probe_media(p,'text/plain')
    assert len(m['preview'])==1600
    assert '营业执照' in m['text'] and len(m['text'])>2000


def test_text_probe_indexes_content_beyond_display_window(tmp_path):
    p=tmp_path/'very-long.log';p.write_text('x'*30000+'\n订单 ORDER-998877 未收到货',encoding='utf-8')
    m=probe_media(p,'text/plain')
    assert len(m['text'])==24000
    assert 'ORDER-998877' in m['search_text']


def test_excel_indexes_rows_well_beyond_old_eighty_row_limit(tmp_path):
    from openpyxl import Workbook
    p=tmp_path/'orders.xlsx';wb=Workbook();ws=wb.active
    for i in range(1,151):ws.append([i,'普通记录'])
    ws.append([151,'订单 ORDER-551199 未收到货'])
    wb.save(p)
    m=probe_media(p,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    assert m['indexed_rows']>=151 and 'ORDER-551199' in m['search_text']
