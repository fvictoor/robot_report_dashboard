import sys
import os
import pandas as pd
from jinja2 import Environment, FileSystemLoader
import datetime
from collections import defaultdict
from robot.api import ExecutionResult, ResultVisitor
import argparse
import json
import xml.etree.ElementTree as ET

class ModelCollector(ResultVisitor):
    def __init__(self):
        self.tests = []
        self.suite_setup_failed = False

    def start_suite(self, suite):
        if suite.setup and suite.setup.status == 'FAIL':
            self.suite_setup_failed = True
        else:
            self.suite_setup_failed = False

    def visit_test(self, test):
        suite_name = test.parent.longname if hasattr(test.parent, 'longname') else "Desconhecida"
        status = 'FAIL' if self.suite_setup_failed else test.status
        message = test.message
        if self.suite_setup_failed and not message:
            message = "Test failed due to suite setup failure."

        self.tests.append({
            "name": test.name,
            "suite": suite_name,
            "status": status,
            "starttime": test.starttime,
            "endtime": test.endtime,
            "elapsed": time_diff(test.starttime, test.endtime),
            "tags": list(test.tags),
            "doc": test.doc,
            "message": message
        })

def format_seconds_to_hms(seconds):
    return str(datetime.timedelta(seconds=int(seconds)))

def time_diff(start, end):
    try:
        start_dt = datetime.datetime.strptime(start, "%Y%m%d %H:%M:%S.%f")
        end_dt = datetime.datetime.strptime(end, "%Y%m%d %H:%M:%S.%f")
        return (end_dt - start_dt).total_seconds()
    except (TypeError, ValueError):
        return 0.0

def extract_results(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Arquivo não encontrado: {path}")
    result = ExecutionResult(path)
    collector = ModelCollector()
    result.visit(collector)
    total_elapsed_time = result.suite.elapsedtime / 1000
    execution_date = datetime.datetime.strptime(result.suite.starttime, "%Y%m%d %H:%M:%S.%f").strftime("%d/%m/%Y")
    return collector.tests, total_elapsed_time, execution_date

def extract_config_info(xml_path):
    config_info = {
        "browser": "Não encontrado",
        "resolution": "Não encontrada",
        "frontend_url": "Não encontrada",
        "backend_url": "Não encontrada"
    }
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()

        # Encontra o browser e a resolução
        for msg in root.findall(".//kw[@name='New Context']/msg[@level='INFO']"):
            if 'viewport' in msg.text:
                try:
                    data = json.loads(msg.text)
                    if 'viewport' in data and 'width' in data['viewport'] and 'height' in data['viewport']:
                        vp = data['viewport']
                        config_info['resolution'] = f"{vp['width']}x{vp['height']}"
                    if 'browser' in data:
                        config_info['browser'] = data.get('browser', config_info['browser'])
                    break 
                except json.JSONDecodeError:
                    continue
        
        for msg in root.findall(".//kw[@name='New Browser']/msg[@level='INFO']"):
             if 'browser' in msg.text:
                try:
                    data = json.loads(msg.text)
                    if 'browser' in data:
                         config_info['browser'] = data['browser']
                         break
                except json.JSONDecodeError:
                    continue

        # Encontra URL de Frontend (LÓGICA CORRIGIDA)
        for msg in root.findall(".//kw[@name='New Page']/msg"):
            if 'opened url:' in msg.text:
                url_part = msg.text.split(' ')[-1]
                base_url = "/".join(url_part.split('/')[:3])
                config_info['frontend_url'] = base_url
                break

        # Encontra URL de Backend
        for msg in root.findall(".//kw[@owner='RequestsLibrary']/msg"):
            if 'Request : url=' in msg.text:
                url_part = msg.text.split('url=')[1].strip()
                base_url = "/".join(url_part.split('/')[:3])
                config_info['backend_url'] = base_url
                break
                
    except Exception:
        pass
        
    return config_info

def generate_dashboard(tests1, total_time1, exec_date1, tests2, tags_to_track, output_dir, filename, has_rerun_file, config_info):
    df_main = pd.DataFrame(tests1)
    
    total_tests = len(df_main)
    initial_failures_df = df_main[df_main['status'] == 'FAIL']
    initial_failures = len(initial_failures_df)
    total_passed = total_tests - initial_failures

    if has_rerun_file:
        df_rerun = pd.DataFrame(tests2)
        failed_rerun_df = df_rerun[df_rerun['status'] == 'FAIL']
        permanent_failures_df = pd.merge(initial_failures_df, failed_rerun_df, on='name', how='inner')
        permanent_failure_names = set(permanent_failures_df['name'])
        final_failures = len(permanent_failure_names)
        recovered = initial_failures - final_failures
    else:
        recovered = 0
        final_failures = initial_failures
        permanent_failure_names = set(initial_failures_df['name'])

    status_distribution = {
        "labels": ["Aprovados", "Recuperados", "Falhas Definitivas"],
        "data": [total_passed, recovered, final_failures]
    }

    tag_results = defaultdict(lambda: {'passed': 0, 'failed': 0})
    for test in tests1:
        is_permanent_failure = test['name'] in permanent_failure_names
        status_for_tag = 'failed' if test['status'] == 'FAIL' and is_permanent_failure else 'passed'
        for tag in test['tags']:
            if tag in tags_to_track:
                if status_for_tag == 'passed':
                    tag_results[tag]['passed'] += 1
                else:
                    tag_results[tag]['failed'] += 1

    tag_chart_data = {
        "labels": tags_to_track,
        "passed_data": [tag_results[tag]['passed'] for tag in tags_to_track],
        "failed_data": [tag_results[tag]['failed'] for tag in tags_to_track]
    }

    suite_times = defaultdict(float)
    for test in tests1:
        suite_times[test['suite']] += test['elapsed']
    
    suite_time_data_sorted = sorted(suite_times.items(), key=lambda item: item[1], reverse=True)
    suite_time_chart_data = {
        "labels": [item[0] for item in suite_time_data_sorted],
        "data": [item[1] for item in suite_time_data_sorted],
        "formatted_times": [format_seconds_to_hms(item[1]) for item in suite_time_data_sorted]
    }

    tag_times = defaultdict(float)
    for test in tests1:
        for tag in test['tags']:
            if tag in tags_to_track:
                tag_times[tag] += test['elapsed']
                
    tag_time_data_filtered = {tag: tag_times[tag] for tag in tags_to_track if tag in tag_times}
    tag_time_data_sorted = sorted(tag_time_data_filtered.items(), key=lambda item: item[1], reverse=True)
    tag_time_chart_data = {
        "labels": [item[0] for item in tag_time_data_sorted],
        "data": [item[1] for item in tag_time_data_sorted],
        "formatted_times": [format_seconds_to_hms(item[1]) for item in tag_time_data_sorted]
    }

    test_details_by_suite = defaultdict(list)
    for test in tests1:
        final_status = 'PASS'
        if test['name'] in permanent_failure_names:
            final_status = 'FAIL'
        
        test_details_by_suite[test["suite"]].append({
            "name": test["name"], "status": final_status, "elapsed": format_seconds_to_hms(test["elapsed"]),
            "tags": test["tags"], "doc": test["doc"], "message": test["message"]
        })

    suite_list_for_template = []
    for suite_name, tests in test_details_by_suite.items():
        has_failures = any(test['status'] == 'FAIL' for test in tests)
        suite_list_for_template.append({
            "name": suite_name, "tests": tests, "has_failures": has_failures
        })
    
    template_dir = os.path.join(os.path.dirname(__file__), "templates")
    env = Environment(loader=FileSystemLoader(template_dir))
    template = env.get_template("modern_report_template.html")
    
    html = template.render(
        total_tests=total_tests, total_passed=total_passed, initial_failures=initial_failures,
        recovered=recovered, final_failures=final_failures, total_execution_time=format_seconds_to_hms(total_time1),
        execution_date=exec_date1,
        status_distribution_json=json.dumps(status_distribution),
        tag_chart_data_json=json.dumps(tag_chart_data),
        suite_time_chart_data_json=json.dumps(suite_time_chart_data),
        tag_time_chart_data_json=json.dumps(tag_time_chart_data),
        suite_list=suite_list_for_template,
        config_info=config_info
    )

    os.makedirs(output_dir, exist_ok=True)
    
    if not filename.lower().endswith('.html'):
        filename += '.html'
        
    output_path = os.path.join(output_dir, filename)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
        
    print(f"Dashboard customizado gerado: {os.path.abspath(output_path)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Gera um dashboard HTML moderno a partir de resultados do Robot Framework.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument('output_xml', help='Caminho para o arquivo output.xml principal.')
    parser.add_argument('rerun_xml', help='Caminho para o arquivo output.xml do rerun.')
    parser.add_argument(
        '--tags', 
        help='Lista de tags de prioridade separadas por vírgula para os gráficos.\nExemplo: "alta,media,baixa"',
        default='alta,media,baixa'
    )
    parser.add_argument(
        '--output_dir',
        help='Pasta de destino para o arquivo de relatório.\nO padrão é o diretório atual.\nExemplo: "C:\\Relatorios"',
        default='.'
    )
    parser.add_argument(
        '--filename',
        help='Nome do arquivo HTML gerado.\nO padrão é "dashboard_customizado.html".\nExemplo: "meu_relatorio.html"',
        default='dashboard_customizado.html'
    )

    args = parser.parse_args()
    
    tags_list = [tag.strip() for tag in args.tags.split(',') if tag.strip()]

    try:
        tests_main, total_time_main, exec_date_main = extract_results(args.output_xml)
        
        config_info = extract_config_info(args.output_xml)

        tests_rerun = []
        has_rerun_file = os.path.exists(args.rerun_xml)

        if has_rerun_file:
            tests_rerun, _, _ = extract_results(args.rerun_xml)
        else:
            print(f"Atenção: O arquivo de rerun '{args.rerun_xml}' não foi encontrado.")
            print("         O dashboard será gerado sem a análise de recuperação de falhas.")

        generate_dashboard(
            tests_main, 
            total_time_main, 
            exec_date_main, 
            tests_rerun, 
            tags_list, 
            args.output_dir, 
            args.filename,
            has_rerun_file,
            config_info
        )
    except Exception as e:
        print(f"Erro ao gerar relatório: {e}")
        sys.exit(1)