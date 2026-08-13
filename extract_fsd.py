import zipfile
import xml.etree.ElementTree as ET

docx_path = r'D:\data-pipeline-mvp\data\sample\Data_Integration_Pipeline_ETL_FSD_v0.2.docx'
output_path = r'D:\data-pipeline-mvp\data\sample\FSD_extracted.txt'

z = zipfile.ZipFile(docx_path)
xml_content = z.read('word/document.xml')
root = ET.fromstring(xml_content)

ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
paragraphs = root.findall('.//w:p', ns)

lines = []
for p in paragraphs:
    texts = [node.text for node in p.findall('.//w:t', ns) if node.text]
    line = ''.join(texts)
    lines.append(line)

# Also extract tables
tables = root.findall('.//w:tbl', ns)
for tbl in tables:
    rows = tbl.findall('.//w:tr', ns)
    for row in rows:
        cells = row.findall('.//w:tc', ns)
        cell_texts = []
        for cell in cells:
            cell_paragraphs = cell.findall('.//w:p', ns)
            cell_lines = []
            for cp in cell_paragraphs:
                texts = [node.text for node in cp.findall('.//w:t', ns) if node.text]
                cell_lines.append(''.join(texts))
            cell_texts.append(' | '.join(cell_lines))
        lines.append(' | '.join(cell_texts))

with open(output_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))

print(f"Extracted {len(lines)} lines to {output_path}")