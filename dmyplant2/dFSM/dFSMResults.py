import pandas as pd
import numpy as np
from collections import namedtuple
from IPython.display import HTML, display
from .dFSM import filterFSM


# RUN2 Results
def detect_edge_right(data, name, startversuch=pd.DataFrame([]), right=None):
    right = startversuch['endtime'] if not startversuch.empty else right
    ndata = data[data['datetime'] < right].copy() if right != None else data.copy()
    fac = {'left': -1.0, 'right': 1.0}
    ldata = ndata[['datetime',name]]
    x0 = ldata.iloc[0]['datetime'];
    x1 = ldata.iloc[-1]['datetime'];
    edge0 = ndata.loc[ndata[name].idxmax()]
    try:
        xfac = (x1 - x0) / (x1 - edge0.datetime)
    except ZeroDivisionError:
        xfac = 0.0
    xfac = min(xfac, 150.0)
    #print(f"###### | xfac: {xfac:5.2f} | kind: {kind:>5} | name: {name}")
    lmax = ldata.loc[:,name].max() * xfac * 0.90
    ndata['helpline_right'] = (ndata['datetime'] - x0)*lmax/(x1-x0)
    ndata[name+'_right'] = ndata[name]+(ndata['datetime'] - x0)*lmax/(x1-x0)
    Point = namedtuple('edge',["loc", "val"])
    try:
        edge = ndata.loc[ndata[name+'_right'].idxmax()]
    except Exception as err:
        #logging.error(str(err))
        edge = ndata.iloc[-1]
    return  Point(edge.datetime, ldata.at[edge.name,name]), ndata

def detect_edge_left(data, name, startversuch=pd.DataFrame([]), left=None):
    left = startversuch['starttime'] if not startversuch.empty else left
    ndata = data[data['datetime'] > left].copy() if left != None else data.copy()
    ldata = ndata[['datetime',name]]
    x0 = ldata.iloc[0]['datetime'];
    x1 = ldata.iloc[-1]['datetime'];
    edge0 = ndata.loc[ndata[name].idxmax()]
    try:
        xfac = (x1 - x0) / (edge0.datetime - x0)
    except ZeroDivisionError:
        xfac = 0.0
    xfac = min(xfac, 20.0)
    #print(f"###### | xfac: {xfac:5.2f} | left | name: {name}")
    lmax = ldata.loc[:,name].max() * xfac * 0.90
    ndata['helpline_left'] = (x0 - ndata['datetime'])*lmax/(x1-x0) + lmax
    ndata[name+'_left'] = ndata[name]+(x0 - ndata['datetime'])*lmax/(x1-x0) + lmax
    Point = namedtuple('edge',["loc", "val"])
    try:
        edge = ndata.loc[ndata[name+'_left'].idxmax()]
    except Exception as err:
        #logging.error(str(err))
        edge = ndata.iloc[-1]
    return  Point(edge.datetime, ldata.at[edge.name,name]), ndata

## Resultate aus einem FSM Lauf ermitteln.
def disp_result(startversuch):
    summary = pd.DataFrame(startversuch[filterFSM.run2filter_content]).T
    #summary = pd.DataFrame.from_dict({k:v for k,v in dict(startversuch[['index'] + fsm.filters['run2filter_times']]).items() if v == v}, orient='index').T.round(2)
    #summary = pd.DataFrame(startversuch[fsm.filters['run2filter_times']], dtype=np.float64).fillna(0).round(2).T
    display(HTML(summary.to_html(escape=False, index=False)))
    #display(HTML('<h3>'+ summary.to_html(escape=False, index=False) + '</h3>'))

def disp_alarms(startversuch):
    ald = []; alt = []
    for al in startversuch['alarms']:
            ald.append({
                    'state':al['state'],'severity':al['msg']['severity'],'Number':al['msg']['name'],
                    'date':pd.to_datetime(int(al['msg']['timestamp'])*1e6).strftime('%d.%m.%Y %H:%M:%S'),
                    'message':al['msg']['message']
            })
            alt.append(pd.to_datetime(int(al['msg']['timestamp'])*1e6))
    aldf = pd.DataFrame(ald)
    if not aldf.empty:
        display(HTML(aldf.to_html(escape=False, index=False)))
        #display(HTML('<h3>'+ aldf.to_html(escape=False, index=False) + '</h3>'))
    return alt

def disp_warnings(startversuch):
    wad = []; wat = []
    for wd in startversuch['warnings']:
            wad.append({
                    'state':wd['state'],'severity':wd['msg']['severity'],'Number':wd['msg']['name'],
                    'date':pd.to_datetime(int(wd['msg']['timestamp'])*1e6).strftime('%d.%m.%Y %H:%M:%S'),
                    'message':wd['msg']['message']
            })
            wat.append(pd.to_datetime(int(wd['msg']['timestamp'])*1e6))
    wdf = pd.DataFrame(wad)
    if not wdf.empty:
        display(HTML(wdf.to_html(escape=False, index=False)))
        #display(HTML('<h3>'+ wdf.to_html(escape=False, index=False) + '</h3>'))
    return wat 


def _pareto(mm):
    unique_res = set([msg['name'] for msg in mm])
    res = [{ 'anz': len([msg for msg in mm if msg['name'] == m]),
                'severity': mm[0]['severity'],
                'number':m,
                'msg':f"{str([msg['message'] for msg in mm if msg['name'] == m][0]):>}"
            } for m in unique_res]
    return sorted(res, key=lambda x:x['anz'], reverse=True)        

def _states_pareto(fsm, severity, states = []):
    rmessages = []
    if type(states) == str:
        states = [states]
    for state in states:
        rmessages += [msg for msg in fsm.states[state]._messages if msg['severity'] == severity]
    return _pareto(rmessages)

def alarms_pareto(fsm, states):
    return pd.DataFrame(_states_pareto(fsm, 800, states))

def warnings_pareto(fsm, states):
    return pd.DataFrame(_states_pareto(fsm, 700, states))

def summary(fsm):
    display(HTML(
        f"""
        <h2>{str(fsm._e)}</h2>
        <br>
        <table>
            <thead>
                <tr>
                    <td></td>
                    <td>From</td>
                    <td>To</td>
                    <td>Days</td>
                </tr>
            </thead>
            <tr>
                <td>Interval</td>
                <td>{fsm.first_message:%d.%m.%Y}</td>
                <td>{fsm.last_message:%d.%m.%Y}</td>
                <td>{fsm.period.days:5}</td>
        </td>
            </tr>
        </table>
        """))
    nsummary = []
    res = fsm.result
    for mode in ['???','OFF','MANUAL', 'AUTO']:
        lstarts = res[res['mode'] == mode].shape[0]
        successful_starts = res[((res.success) & (res['mode'] == mode))].shape[0]
        nsummary.append([lstarts, successful_starts,(successful_starts / lstarts) * 100.0 if lstarts != 0 else 0.0])
    nsummary.append([res.shape[0],res[res.success].shape[0],(res[res.success].shape[0] / res.shape[0]) * 100.0])
    display(HTML(pd.DataFrame(nsummary, index=['???','OFF','MANUAL', 'AUTO','ALL'],columns=['Starts','successful','%'], dtype=np.int64).to_html(escape=False)))

def summary_out(fsm):
    fsum = f"""
        <table>
            <thead>
                <tr>
                    <td></td>
                    <td>From</td>
                    <td>To</td>
                    <td>Days</td>
                </tr>
            </thead>
            <tr>
                <td>{fsm._e['Engine ID']}</td>
                <td>{fsm.first_message:%d.%m.%Y}</td>
                <td>{fsm.last_message:%d.%m.%Y}</td>
                <td>{fsm.period.days:5}</td>
        </td>
            </tr>
        </table>
        <br>
        """
    nsummary = []
    res = fsm.result
    for mode in ['???','OFF','MANUAL', 'AUTO']:
        lstarts = res[res['mode'] == mode].shape[0]
        successful_starts = res[((res.success) & (res['mode'] == mode))].shape[0]
        nsummary.append([lstarts, successful_starts,(successful_starts / lstarts) * 100.0 if lstarts != 0 else 0.0])
    nsummary.append([res.shape[0],res[res.success].shape[0],(res[res.success].shape[0] / res.shape[0]) * 100.0])
    display(HTML(fsum + pd.DataFrame(nsummary, index=['???','OFF','MANUAL', 'AUTO','ALL'],columns=['Starts','successful','%'], dtype=np.int64).to_html(escape=False)))


# alter Code
#     def completed(fsm, limit_to = 10):

#         def filter_messages(messages, severity):
#             fmessages = [msg for msg in messages if msg['severity'] == severity]
#             unique_messages = set([msg['name'] for msg in fmessages])
#             res_messages = [{ 'anz': len([msg for msg in fmessages if msg['name'] == m]), 
#                               'msg':f"{m} {[msg['message'] for msg in fmessages if msg['name'] == m][0]}"
#                             } for m in unique_messages]
#             return len(fmessages), sorted(res_messages, key=lambda x:x['anz'], reverse=True) 

#         print(f'''

# *****************************************
# * Ergebnisse (c)2022 Dieter Chvatal     *
# *****************************************
# gesamter Zeitraum: {fsm._period.round('S')}

# ''')
#         for state in fsm.states:

#             alarms, alu = filter_messages(fsm.states[state]._messages, 800)
#             al = "".join([f"{line['anz']:3d} {line['msg']}\n" for line in alu[:limit_to]])

#             warnings, wru = filter_messages(fsm.states[state]._messages, 700)
#             wn = "".join([f"{line['anz']:3d} {line['msg']}\n" for line in wru[:limit_to]])

#             print(
# f"""
# {state}:
# Dauer       : {str(fsm.states[state].get_duration().round('S')):>20}  
# Anteil      : {fsm.states[state].get_duration()/fsm._whole_period*100.0:20.2f}%
# Messages    : {len(fsm.states[state]._messages):20} 
# Alarms total: {alarms:20d}
#       unique: {len(alu):20d}

# {al}

# Warnings total: {warnings:20d}
#         unique: {len(wru):20d}

# {wn}
# """)
#         print('completed')
