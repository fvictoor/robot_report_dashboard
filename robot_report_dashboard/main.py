import sys
import os
import pandas as pd
from jinja2 import Environment, FileSystemLoader
import datetime
from collections import defaultdict
from robot.api import ExecutionResult, ResultVisitor
import argparse

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

# MELHORIA 1: A função agora aceita também o nome do arquivo (filename)
def generate_dashboard(tests1, total_time1, exec_date1, tests2, tags_to_track, output_dir, filename):
    df_main = pd.DataFrame(tests1)
    df_rerun = pd.DataFrame(tests2)

    initial_failures_df = df_main[df_main['status'] == 'FAIL']
    failed_rerun_df = df_rerun[df_rerun['status'] == 'FAIL']
    permanent_failures_df = pd.merge(initial_failures_df, failed_rerun_df, on='name', how='inner')
    permanent_failure_names = set(permanent_failures_df['name'])

    test_details_by_suite = defaultdict(list)
    for test in tests1:
        test_details_by_suite[test["suite"]].append({
            "name": test["name"], "status": test["status"], "elapsed": format_seconds_to_hms(test["elapsed"]),
            "tags": test["tags"], "doc": test["doc"], "message": test["message"]
        })

    suite_list_for_template = []
    for suite_name, tests in test_details_by_suite.items():
        has_failures = any(test['name'] in permanent_failure_names for test in tests)
        suite_list_for_template.append({
            "name": suite_name,
            "tests": tests,
            "has_failures": has_failures
        })

    total_tests = len(df_main)
    initial_failures = len(initial_failures_df)
    total_passed = total_tests - initial_failures
    final_failures = len(permanent_failure_names)
    recovered = initial_failures - final_failures
    
    suite_counts = defaultdict(int)
    suite_times = defaultdict(float)
    for test in tests1:
        suite = test["suite"]
        suite_counts[suite] += 1
        suite_times[suite] += test["elapsed"]

    suite_data = [{"suite": s, "count": c, "time": suite_times[s]} for s, c in suite_counts.items()]
    for s_data in suite_data:
        s_data['time_hms'] = format_seconds_to_hms(s_data['time'])
    suite_data_sorted_by_time = sorted(suite_data, key=lambda x: x['time'], reverse=True)

    tag_counts = defaultdict(int)
    tag_times = defaultdict(float)
    for test in tests1:
        for tag in test['tags']:
            if tag in tags_to_track:
                tag_counts[tag] += 1
                tag_times[tag] += test['elapsed']

    tag_count_data = [{"tag": tag, "count": tag_counts.get(tag, 0)} for tag in tags_to_track]

    tag_time_data = [{"tag": tag, "time": tag_times.get(tag, 0)} for tag in tags_to_track]
    for t_data in tag_time_data:
        t_data['time_hms'] = format_seconds_to_hms(t_data['time'])
    tag_time_data_sorted = sorted(tag_time_data, key=lambda x: x['time'], reverse=True)

    template_dir = os.path.join(os.path.dirname(__file__), "templates")
    env = Environment(loader=FileSystemLoader(template_dir))
    template = env.get_template("report_template.html")
    
    html = template.render(
        total_tests=total_tests, total_passed=total_passed, initial_failures=initial_failures,
        recovered=recovered, final_failures=final_failures, total_execution_time=format_seconds_to_hms(total_time1),
        execution_date=exec_date1, suite_data=suite_data, suite_data_sorted_by_time=suite_data_sorted_by_time,
        tag_count_data=tag_count_data,
        tag_time_data_sorted=tag_time_data_sorted,
        suite_list=suite_list_for_template
    )

    os.makedirs(output_dir, exist_ok=True)
    
    # MELHORIA 2: Garante que a extensão .html seja adicionada se não for fornecida
    if not filename.lower().endswith('.html'):
        filename += '.html'
        
    output_path = os.path.join(output_dir, filename)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
        
    print(f"✅ Relatório gerado: {os.path.abspath(output_path)}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Gera um dashboard HTML a partir de resultados do Robot Framework.",
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
    # MELHORIA 3: Novo argumento para definir o nome do arquivo
    parser.add_argument(
        '--filename',
        help='Nome do arquivo de relatório HTML gerado.\nO padrão é "report.html".\nExemplo: "meu_relatorio.html"',
        default='report.html'
    )

    args = parser.parse_args()
    
    tags_list = [tag.strip() for tag in args.tags.split(',') if tag.strip()]

    try:
        tests_main, total_time_main, exec_date_main = extract_results(args.output_xml)
        tests_rerun, _, _ = extract_results(args.rerun_xml)
        # Passa todos os argumentos para a função
        generate_dashboard(
            tests_main, 
            total_time_main, 
            exec_date_main, 
            tests_rerun, 
            tags_list, 
            args.output_dir, 
            args.filename
        )
    except Exception as e:
        print(f"❌ Erro ao gerar relatório: {e}")
        sys.exit(1)