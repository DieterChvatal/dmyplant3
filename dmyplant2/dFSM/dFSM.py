import copy
from datetime import datetime
import logging
import os
import pickle
import warnings
from pprint import pprint as pp, pformat as pf

import arrow
import dmyplant2
import numpy as np
import pandas as pd
from tqdm.auto import tqdm

warnings.simplefilter(action='ignore', category=FutureWarning)

#Various_Bits_CollAlarm
class StateVector:
    statechange = False
    startno = 0
    laststate = ''
    laststate_start = None,
    currentstate = ''
    currentstate_start = None
    in_operation = ''
    service_selector = ''
    msg = None

    def pp(self):
        print(
f"""
       statechange: {self.statechange}
           startno: {self.startno}
         laststate: {self.laststate}
   laststate_start: {self.laststate_start}
      currentstate: {self.currentstate}
currentstate_start: {self.currentstate_start}
      in_operation: {self.in_operation}
  service_selector: {self.service_selector}
               msg: {self.msg['severity']} 
                    {pd.to_datetime(int(self.msg['timestamp'])*1e6).strftime('%d.%m.%Y %H:%M:%S')}  
                    {self.msg['name']} 
                    {self.msg['message']}
""")

    def __str__(self):
        return  f"{'*' if self.statechange else '':2}|"+ \
                f"{self.startno:04}| " + \
                f"{self.laststate_start.strftime('%d.%m %H:%M:%S')} " + \
                f"{self.laststate:18}| " + \
                f"{self.currentstate_start.strftime('%d.%m %H:%M:%S')} " + \
                f"{self.currentstate:18}| " + \
                f"{self.in_operation:4}| " + \
                f"{self.service_selector:6}| " + \
                f"{self.msg['severity']} {pd.to_datetime(int(self.msg['timestamp'])*1e6).strftime('%d.%m.%Y %H:%M:%S')} {self.msg['name']} {self.msg['message']}"


# States und Transferfunktionen, Sammeln von Statebezogenen Daten ... 
class State:
    def __init__(self, statename, transferfun_list):
        self._statename = statename
        self._transferfunctions = transferfun_list
        self._trigger = False
    
    def send(self,msg):
        for transfun in self._transferfunctions: # screen triggers
            self._trigger = msg['name'] == transfun['trigger'][:4]
            if self._trigger:
                return transfun['new-state']
        return self._statename

    def update_vector_on_statechange(self, vector):
        vector.laststate = self._statename
        vector.laststate_start = vector.currentstate_start
        vector.currentstate_start = pd.to_datetime(vector.msg['timestamp'] * 1e6)
        return vector        

    def trigger_on_vector(self, vector):
        vector.currentstate = self.send(vector.msg)
        vector.statechange = self._trigger
        if self._trigger:
            vector = self.update_vector_on_statechange(vector)
        return [vector]

# SpezialFall Loadram, hier wird ein berechneter Statechange ermittelt.
class LoadrampState(State):
    def __init__(self, statename, transferfun_list, e):
        self._e = e
        self._full_load_timestamp = None
        self._loadramp = self._e['rP_Ramp_Set'] or 0.625 # %/sec
        self._default_ramp_duration = 100.0 / self._loadramp
        super().__init__(statename, transferfun_list)

    def trigger_on_vector(self, vector):
        #print(vector)
        vectorlist = super().trigger_on_vector(vector)
        vector = vectorlist[0]

        # one of the triggerfunctions has already changed state. 
        if vector.statechange: 
            self._full_load_timestamp = None
            return [vector]

        # calculate the end of ramp time.
        if self._full_load_timestamp == None:
            self._full_load_timestamp = int((vector.currentstate_start.timestamp() + self._default_ramp_duration) * 1e3)

        # use the message target load reached to make the trigger more accurate. (This message isnt available on all engines.)
        if vector.msg['name'] == '9047':
            self._full_load_timestamp = vector.msg['timestamp']

        # trigger on the firstmessage after the calcalulated event time, switch to 'targetoperation'
        # insert a virtual message before the received message exactly at _full_load_timestamp
        if self._full_load_timestamp != None and int(vector.msg['timestamp']) >= self._full_load_timestamp:
                
                # change the state, because we do the statechange in vector 1 
                vector2 = self.update_vector_on_statechange(vector)
                vector2.currentstate = 'targetoperation'
                
                # copy state vector, fill out the relevant data and trigger to tagetopeartion
                vector1 = copy.deepcopy(vector2)
                vector1.msg = {'name':'9047', 'message':'Target load reached (calculated)','timestamp':self._full_load_timestamp,'severity':600}
                vector1.statechange = True
                vector1.currentstate = 'targetoperation'
                vector1.currentstate_start = pd.to_datetime(self._full_load_timestamp * 1e6)

                # Reset the State for the next event.
                self._full_load_timestamp = None

                # and deliver both events back to the main loop
                return [vector1,vector2]
        
        # just pass through state vectors in all other cases.
        return [vector]
class FSM:
    def __init__(self, e):
        self._e = e
        self._initial_state = 'standstill'
        self._states = {
                'standstill': State('standstill',[
                    { 'trigger':'1231 Request module on', 'new-state': 'startpreparation'},            
                    ]),
                'startpreparation': State('startpreparation',[
                    { 'trigger':'1249 Starter on', 'new-state': 'starter'},
                    { 'trigger':'1232 Request module off', 'new-state': 'standstill'}
                    ]),
                'starter': State('starter',[
                    { 'trigger':'3225 Ignition on', 'new-state':'speedup'},
                    { 'trigger':'1232 Request module off', 'new-state':'standstill'}
                    ]),
                'speedup': State('speedup',[
                    { 'trigger':'2124 Idle', 'new-state':'idle'},
                    { 'trigger':'2139 Request Synchronization', 'new-state':'synchronize'}, 
                    { 'trigger':'3226 Ignition off', 'new-state':'standstill'}
                    ]),             
                'idle': State('idle',[
                    { 'trigger':'2139 Request Synchronization', 'new-state':'synchronize'},
                    { 'trigger':'3226 Ignition off', 'new-state':'standstill'}
                    ]),
                'synchronize': State('synchronize',[
                    { 'trigger':'1235 Generator CB closed', 'new-state':'loadramp'},                
                    { 'trigger':'3226 Ignition off', 'new-state':'standstill'}
                    ]),             
                'loadramp': LoadrampState('loadramp',[
                    { 'trigger':'3226 Ignition off', 'new-state':'standstill'},
                    { 'trigger':'1232 Request module off', 'new-state':'rampdown'},
                    { 'trigger':'Calculated statechange', 'new-state':'targetoperation'},
                    ], e),             
                'targetoperation': State('targetoperation',[
                    { 'trigger':'1232 Request module off', 'new-state':'rampdown'},
                    { 'trigger':'1236 Generator CB opened', 'new-state':'idle'},
                    ]),
                'rampdown': State('rampdown',[
                    { 'trigger':'1236 Generator CB opened', 'new-state':'coolrun'},
                    { 'trigger':'3226 Ignition off', 'new-state':'standstill'},
                    { 'trigger':'1231 Request module on', 'new-state':'targetoperation'},
                    ]),
                'coolrun': State('coolrun',[
                    { 'trigger':'1234 Operation off', 'new-state':'runout'},
                    { 'trigger':'3226 Ignition off', 'new-state':'standstill'}
                    ]),
                'runout': State('runout',[
                    { 'trigger':'3226 Ignition off', 'new-state':'standstill'},
                    { 'trigger':'1231 Request module on', 'new-state': 'startpreparation'},            
                ])
            }

    @property
    def initial_state(self):
        return self._initial_state

    @property
    def states(self):
        return self._states

    def dot(self, fn):
        """Create a FSM Diagram of specified states in *.dot Format
        Args:
            fn : Filename
        """
        with open(fn, 'w') as f:
            f.write("digraph G {\n")
            f.write('    graph [rankdir=TB labelfontcolor=red fontname="monospace" nodesep=1 size="20,33"]\n')
            f.write('    node [fontname="monospace" fontsize=10  shape="circle"]\n')
            f.write('    edge [fontname="monospace" color="grey" fontsize=10]\n')
            for s in self._states:
                f.write(f'    {s.replace("-","")} [label="{s}"]\n')
                for t in self._states[s]._transferfunctions:
                    f.write(f'    {s.replace("-","")} -> {t["new-state"].replace("-","")} [label="{t["trigger"]}"]\n')
            f.write("}\n")


class filterFSM:
    run2filter_content = ['no','success','mode','startpreparation','starter','speedup','idle','synchronize','loadramp','cumstarttime','maxload','ramprate','targetoperation','rampdown','coolrun','runout','count_alarms', 'count_warnings']
    vertical_lines_times = ['startpreparation','starter','speedup','idle','synchronize','loadramp','targetoperation','rampdown','coolrun','runout']

class msgFSM:
    def __init__(self, e, p_from = None, p_to=None, skip_days=None, frompickle='NOTIMPLEMENTED',successtime=600):
        self._e = e
        self._successtime = successtime
        self.load_messages(e, p_from, p_to, skip_days)
        self._pre_period = 5*60 #sec 'prerun' in data download Start before cycle start event.
        self._post_period = 21*60 #sec 'postrun' in data download Start after cycle stop event.
        #self._pre_period = 0 #sec 'prerun' in data download Start before cycle start event.
        #self._post_period = 0 #sec 'postrun' in data download Start after cycle stop event.

        fsmStates = FSM(self._e)
        fsmStates.dot('FSM.dot')
        self.states = fsmStates.states

        self.svec = StateVector()
        self.svec.statechange = True
        self.svec.laststate = 'init'
        self.svec.laststate_start = self.first_message
        self.svec.currentstate = fsmStates.initial_state
        self.svec.currentstate_start = self.first_message
        self.svec.in_operation = 'off'
        self.svec.service_selector = '???'

        self.pfn = self._e._fname + '_statemachine.pkl'
        #self._runlog = []
        #self._runlogdetail = []
        self.init_results()

    def init_results(self):
        self.results = {
            'starts': [],
            'starts_counter':0,
            'stops': [
            {
                'run2':False,
                'no': 0,
                'mode': self.svec.service_selector,
                'starttime': self.svec.laststate_start,
                'endtime': pd.Timestamp(0),
                'alarms':[],
                'warnings':[]                
            }],
            'stops_counter':0,
            'runlog': [],
            'runlogdetail': []
        }     

    @property
    def starts(self):
        return pd.DataFrame(self.results['starts'])

    @property
    def stops(self):
        return pd.DataFrame(self.results['stops'])

    def restore(self):
        with open(self.pfn, 'rb') as handle:
            self.results = pickle.load(handle)

    def store(self):
        self.unstore()
        with open(self.pfn, 'wb') as handle:
            pickle.dump(self.results, handle, protocol=4)

    def unstore(self):
        if os.path.exists(self.pfn):
            os.remove(self.pfn)


    ## message handling
    def load_messages(self,e, p_from=None, p_to=None, skip_days=None):
        self._messages = e.get_messages(p_from, p_to)
        pfrom_ts = int(pd.to_datetime(p_from, infer_datetime_format=True).timestamp() * 1000) if p_from else 0
        pto_ts = int(pd.to_datetime(p_to, infer_datetime_format=True).timestamp() * 1000) if p_to else int(pd.Timestamp.now().timestamp() * 1000)
        self._messages = self._messages[(self._messages.timestamp > pfrom_ts) & (self._messages.timestamp < pto_ts)]
        self.first_message = pd.to_datetime(self._messages.iloc[0]['timestamp']*1e6)
        self.last_message = pd.to_datetime(self._messages.iloc[-1]['timestamp']*1e6)
        self._period = pd.Timedelta(self.last_message - self.first_message).round('S')
        if skip_days and not p_from:
            self.first_message = pd.Timestamp(arrow.get(self.first_message).shift(days=skip_days).timestamp()*1e9)
            self._messages = self._messages[self._messages['timestamp'] > int(arrow.get(self.first_message).shift(days=skip_days).timestamp()*1e3)]
        self.count_messages = self._messages.shape[0]

    def msgtxt(self, msg, idx=0):
        return f"{idx:>06} {msg['severity']} {msg['timestamp']} {pd.to_datetime(int(msg['timestamp'])*1e6).strftime('%d.%m.%Y %H:%M:%S')}  {msg['name']} {msg['message']}"

    def msg_smalltxt(self, msg):
        return f"{msg['severity']} {pd.to_datetime(int(msg['timestamp'])*1e6).strftime('%d.%m.%Y %H:%M:%S')}  {msg['name']} {msg['message']}"

    def save_messages(self, fn):
        with open(fn, 'w') as f:
            for index, msg in self._messages.iterrows():
                f.write(self.msgtxt(msg, index)+'\n')
                #f.write(f"{index:>06} {msg['severity']} {msg['timestamp']} {pd.to_datetime(int(msg['timestamp'])*1e6).strftime('%d.%m.%Y %H:%M:%S')}  {msg['name']} {msg['message']}\n")
                if 'associatedValues' in msg:
                    if msg['associatedValues'] == msg['associatedValues']:  # if not NaN ...
                        f.write(f"{pf(msg['associatedValues'])}\n\n")

    def save_runlog(self, fn):
        if len(self._runlog):
            with open(fn, 'w') as f:
                for line in self._runlog:
                    f.write(line + '\n')

    def save_detailrunlog(self, fn):
        if len(self.results['runlogdetail']):
            with open(fn, 'w') as f:
                for vec in self.results['runlogdetail']:
                    f.write(vec.__str__() + '\n')

    def runlogdetail(self, startversuch, statechanges_only = False):
        ts_start = startversuch['starttime'].timestamp() * 1e3
        ts_end = startversuch['endtime'].timestamp() * 1e3
        if statechanges_only:
            log = [x for x in self.results['runlogdetail'] if x.statechange]
        else:
            log = self.results['runlogdetail']
        log = [x for x in log if ((x.msg['timestamp'] >= ts_start) and (x.msg['timestamp'] <= ts_end))]
        return log

#################################################################################################################
### die Finite State Machines:
    def _fsm_Service_selector(self):
        if self.svec.msg['name'] == '1225 Service selector switch Off'[:4]:
            self.svec.service_selector = 'OFF'
        if self.svec.msg['name'] == '1226 Service selector switch Manual'[:4]:
            self.svec.service_selector = 'MANUAL'
        if self.svec.msg['name'] == '1227 Service selector switch Automatic'[:4]:
            self.svec.service_selector = 'AUTO'

    def _fsm_collect_alarms(self):
        key = 'starts' if self.svec.in_operation == 'on' else 'stops'
        if self.svec.msg['severity'] == 800:
            self.results[key][-1]['alarms'].append({
                'state':self.svec.currentstate, 
                'msg': self.svec.msg
                })
        if self.svec.msg['severity'] == 700:
            self.results[key][-1]['warnings'].append({
                'state':self.svec.currentstate, 
                'msg': self.svec.msg
                })

    def _fsm_Operating_Cycle(self):
        if self.svec.statechange:
            if self.svec.currentstate == 'startpreparation':
                self.results['stops'][-1]['endtime'] = self.svec.currentstate_start
                self.results['stops'][-1]['count_alarms'] = len(self.results['stops'][-1]['alarms'])
                self.results['stops'][-1]['count_warnings'] = len(self.results['stops'][-1]['warnings'])
                # apends a new record to the Starts list.
                self.results['starts'].append({
                    'run2':False,
                    'no':self.results['starts_counter'],
                    'success': False,
                    'mode':self.svec.service_selector,
                    'starttime': self.svec.currentstate_start,
                    'endtime': pd.Timestamp(0),
                    'cumstarttime': pd.Timedelta(0),
                    'startpreparation':np.nan,
                    'starter':np.nan,
                    'speedup':np.nan,
                    'idle':np.nan,
                    'synchronize':np.nan,
                    'loadramp':np.nan,
                    'targetoperation':np.nan,
                    'rampdown':np.nan,
                    'coolrun':np.nan,
                    'runout':np.nan,
                    'timing': {},
                    'alarms': [],
                    'warnings': [],
                    'maxload': np.nan,
                    'ramprate': np.nan
                })
                self.results['starts_counter'] += 1 # index for next start
                self.svec.startno = self.results['starts_counter']
                self.svec.in_operation = 'on'
            elif self.svec.in_operation == 'on': # and actstate != FSM.initial_state:
                self.results['starts'][-1]['mode'] = self.svec.service_selector
                rec = {'start':self.svec.laststate_start, 'end':self.svec.currentstate_start}
                if not self.svec.laststate in self.results['starts'][-1]['timing']:
                    self.results['starts'][-1]['timing'][self.svec.laststate]=[rec]
                else:
                    self.results['starts'][-1]['timing'][self.svec.laststate].append(rec)
                #self.results['starts'][-1]['timing']['start_'+ self.svec.laststate] = self.svec.laststate_start 
                #self.results['starts'][-1]['timing']['end_'+ self.svec.laststate] = self.svec.currentstate_start 

            if self.svec.currentstate == 'standstill':
                if self.svec.in_operation == 'on':
                    # start finished
                    self.results['starts'][-1]['endtime'] = self.svec.currentstate_start
                    # calc phase durations
                    sv = self.results['starts'][-1]
                    # phases = [x[6:] for x in self.results['starts'][-1]['timing'] if x.startswith('start_')]
                    phases = list(sv['timing'].keys())

                    # some sense checks, mostly for commissioning or Test cycles 
                    if 'targetoperation' in phases:
                        tlr = sv['timing']['targetoperation']
                        tlr = [{'start':tlr[0]['start'], 'end':tlr[-1]['end']}]
                        sv['timing']['targetoperation_org'] = sv['timing']['targetoperation']
                        sv['timing']['targetoperation'] = tlr
                    # durations = { ph:pd.Timedelta(self.results['starts'][-1]['timing']['end_'+ph] - self.results['starts'][-1]['timing']['start_'+ph]).total_seconds() for ph in phases}
                    durations = { ph:pd.Timedelta(sv['timing'][ph][-1]['end'] - sv['timing'][ph][-1]['start']).total_seconds() for ph in phases}
                    durations['cumstarttime'] = sum([v for k,v in durations.items() if k in ['startpreparation','starter','speedup','idle','synchronize','loadramp']])
                    self.results['starts'][-1].update(durations)
                    if 'targetoperation' in self.results['starts'][-1]:
                        #successful if the targetoperation run was longer than specified
                        self.results['starts'][-1]['success'] = (self.results['starts'][-1]['targetoperation'] > self._successtime)
                    self.results['starts'][-1]['count_alarms'] = len(self.results['starts'][-1]['alarms'])
                    self.results['starts'][-1]['count_warnings'] = len(self.results['starts'][-1]['warnings'])
 
                self.svec.in_operation = 'off'
                self.results['stops_counter'] += 1 # index for next start
                self.results['stops'].append({
                    'run2':False,
                    'no': self.results['stops_counter'],
                    'mode': self.svec.service_selector,
                    'starttime': self.svec.laststate_start,
                    'endtime': pd.Timestamp(0),
                    'alarms':[],
                    'warnings':[]
                })

            _logline= {
                'laststate': self.svec.laststate,
                'laststate_start': self.svec.laststate_start,
                'msg': self.svec.msg['name'] + ' ' + self.svec.msg['message'],
                'currenstate': self.svec.currentstate,
                'currentstate_start': self.svec.currentstate_start,
                'starts': len(self.results['starts']),
                'Successful_starts': len([s for s in self.results['starts'] if s['success']]),
                'operation': self.svec.in_operation,
                'mode': self.svec.service_selector,
            }
            self.results['runlog'].append(_logline)

    def call_trigger_states(self):
        return self.states[self.svec.currentstate].trigger_on_vector(self.svec)

    ## FSM Entry Point.
    def run1(self, enforce=False, silent=False):
        if len(self.results['starts']) == 0 or enforce or not ('run2' in self.results['starts'][0]):
            self.init_results()

            if silent:
                for i, msg in self._messages.iterrows():
                    self.dorun1(msg)

            else:
                #tqdm disturbes the VSC Debugger - disable for debug purposes please.     
                for i,msg in tqdm(self._messages.iterrows(), total=self._messages.shape[0], ncols=80, mininterval=1, unit=' messages', desc="FSM"):
                    self.dorun1(msg)

                # # the FSM statusvector is called self.svec
                # self.svec.msg = msg
                # retsv = self.call_trigger_states()
                # for sv in retsv:   
                #     self.svec = sv
                #     #print(f"{len(self.results['runlogdetail']):5} {sv}")
                #     self.results['runlogdetail'].append(copy.deepcopy(sv))
                #     self._fsm_Service_selector()
                #     self._fsm_collect_alarms()
                #     self._fsm_Operating_Cycle()
    
    def dorun1(self, msg):
        self.svec.msg = msg
        retsv = self.call_trigger_states()
        for sv in retsv:   
            self.svec = sv
            #print(f"{len(self.results['runlogdetail']):5} {sv}")
            self.results['runlogdetail'].append(copy.deepcopy(sv))
            self._fsm_Service_selector()
            self._fsm_collect_alarms()
            self._fsm_Operating_Cycle()

















#********************************************************
    def dorun2(self, index_list, startversuch):
                ii = startversuch['no']
                index_list.append(ii)

                if not startversuch['run2']:

                    data = dmyplant2.get_cycle_data2(self, startversuch, max_length=None, min_length=None, silent=True)

                    if not data.empty:

                        pl, _ = dmyplant2.detect_edge_left(data, 'Power_PowerAct', startversuch)
                        #pr, _ = detect_edge_right(data, 'Power_PowerAct', startversuch)
                        #sl, _ = detect_edge_left(data, 'Various_Values_SpeedAct', startversuch)
                        #sr, _ = detect_edge_right(data, 'Various_Values_SpeedAct', startversuch)

                        self.results['starts'][ii]['title'] = f"{self._e} ----- Start {ii} {startversuch['mode']} | {'SUCCESS' if startversuch['success'] else 'FAILED'} | {startversuch['starttime'].round('S')}"
                        #sv_lines = {k:(startversuch[k] if k in startversuch else np.NaN) for k in filterFSM.vertical_lines_times]}
                        sv_lines = [v for v in startversuch[filterFSM.vertical_lines_times]]
                        start = startversuch['starttime'];
                        
                        # lade die in run1 gesammelten Daten in ein DataFrame, ersetze NaN Werte mit 0
                        backup = {}
                        svdf = pd.DataFrame(sv_lines, index=filterFSM.vertical_lines_times, columns=['FSM'], dtype=np.float64).fillna(0)
                        svdf['RUN2'] = svdf['FSM']

                        # intentionally excluded - Dieter 1.3.2022
                        #if svdf.at['speedup','FSM'] > 0.0:
                        #        svdf.at['speedup','RUN2'] = sl.loc.timestamp() - start.timestamp() - np.cumsum(svdf['RUN2'])['starter']
                        #        svdf.at['idle','RUN2'] = svdf.at['idle','FSM'] - (svdf.at['speedup','RUN2'] - svdf.at['speedup','FSM'])
                        if svdf.at['loadramp','FSM'] > 0.0:
                                calc_loadramp = pl.loc.timestamp() - start.timestamp() - np.cumsum(svdf['RUN2'])['synchronize']
                                svdf.at['loadramp','RUN2'] = calc_loadramp

                                # collect run2 results.
                                backup['loadramp'] = svdf.at['loadramp','FSM'] # alten Wert merken
                                self.results['starts'][ii]['loadramp'] = calc_loadramp

                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore")
                            calc_maxload = pl.val
                            try:
                                calc_ramp = (calc_maxload / self._e['Power_PowerNominal']) * 100 / svdf.at['loadramp','RUN2']
                            except ZeroDivisionError as err:
                                logging.warning(f"calc_ramp: {str(err)}")
                                calc_ramp = np.NaN
                            # doppelte Hosenträger ... hier könnte man ein wenig aufräumen :-)
                            if not np.isfinite(calc_ramp) :
                                calc_ramp = np.NaN

                            backup_cumstarttime = np.cumsum(svdf['FSM'])['loadramp']
                            calc_cumstarttime = np.cumsum(svdf['RUN2'])['loadramp']
                        svdf = pd.concat([
                                svdf, 
                                pd.DataFrame.from_dict(
                                        {       'maxload':['-',calc_maxload],
                                                'ramprate':['-',calc_ramp],
                                                'cumstarttime':[backup_cumstarttime, calc_cumstarttime]
                                        }, 
                                        columns=['FSM','RUN2'],
                                        orient='index')]
                                )
                        #display(HTML(svdf.round(2).T.to_html(escape=False)))

                        # collect run2 results.
                        self.results['starts'][ii]['maxload'] = calc_maxload
                        self.results['starts'][ii]['ramprate'] = calc_ramp
                        backup['cumstarttime'] = backup_cumstarttime
                        self.results['starts'][ii]['cumstarttime'] = calc_cumstarttime

                        self.results['starts'][ii]['backup'] = backup
                        self.results['starts'][ii]['run2'] = True

    def run2(self, rda, silent=False):
        index_list = []
        if silent:
            for n, startversuch in rda.iterrows():
                self.dorun2(index_list, startversuch)
        else:
            for n, startversuch in tqdm(rda.iterrows(), total=rda.shape[0], ncols=80, mininterval=1, unit=' starts', desc="FSM Run2"):
                self.dorun2(index_list, startversuch)
        return pd.DataFrame([self.results['starts'][s] for s in index_list])

############################################################################

# class msgFSM:
#     def __init__(self, e, p_from = None, p_to=None, skip_days=None, frompickle=False, successtime=600):
#         self._e = e
#         self._p_from = p_from
#         self._p_to = p_to
#         self.pfn = self._e._fname + '_statemachine.pkl'
#         self._pre_period = 5*60 #sec 'prerun' in data download Start before cycle start event.
#         self._post_period = 21*60 #sec 'postrun' in data download Start after cycle stop event.
#         self._successtime = successtime

#         self.load_messages(e, p_from, p_to, skip_days)
#         self._data_spec = ['Various_Values_SpeedAct','Power_PowerAct']

#         # Es gibt zwar die message, sie ist aber nicht bei allen Motoren implementiert
#         # und wird zumindest in einem Fall (Forsa Hartmoor, M?) nicht 100% zuverlässig geloggt
#         # daher ist das schätzen und verfeinern in run2 zuverlässiger. 1.3.2033 - Dieter 
#         #self._target_load_message = any(self._messages['name'] == '9047')
#         self._target_load_message = False
#         self._loadramp = self._e['rP_Ramp_Set'] or 0.625 # %/sec
#         self._default_ramp_duration = int(100.0 / self._loadramp * 1e3)
#         self.full_load_timestamp = None
#         # print(f"{'Using' if self._target_load_message else 'Calculating'} '9047 target load reached' Message.")
#         # if not self._target_load_message:
#         #     print(f"load ramp assumed to {self._loadramp} %/sec based on {'rP_Ramp_Set Parameter' if self._e['rP_Ramp_Set'] else 'INNIO standard'}")

#         self.states = FSM.states
#         self.current_state = FSM.initial_state
#         self.act_service_selector = '???'

#         # for initialize some values for collect_data.
#         self._runlog = []
#         self._in_operation = '???'
#         self._timer = pd.Timedelta(0)
#         self.last_ts = pd.to_datetime('01.01.1970')
#         self._starts = []
#         self._starts_counter = 0

#         if frompickle and os.path.exists(self.pfn):
#             with open(self.pfn, 'rb') as handle:
#                 sd = pickle.load(handle)
#                 self._starts = sd['starts']
#                 self.states = sd['states']

#     @property
#     def result(self):
#         for startversuch in self._starts:
#             startversuch['count_alarms'] = len(startversuch['alarms'])
#             startversuch['count_warnings'] = len(startversuch['warnings'])
#         return pd.DataFrame(self._starts)

#     def store(self):
#         sd = {'starts': self._starts, 'states': self.states }
#         self.unstore()
#         with open(self.pfn, 'wb') as handle:
#             pickle.dump(sd, handle, protocol=4)
#             #pickle.dump(self._starts, handle, protocol=4)

#     def unstore(self):
#         if os.path.exists(self.pfn):
#             os.remove(self.pfn)

#     @property
#     def period(self):
#         return self._period

#     ## message handling
#     def load_messages(self,e, p_from, p_to, skip_days):
#         self._messages = e.get_messages(p_from, p_to)
#         self.first_message = pd.Timestamp(self._messages.iloc[0]['timestamp']*1e6)
#         self.last_message = pd.Timestamp(self._messages.iloc[-1]['timestamp']*1e6)
#         self._period = pd.Timedelta(self.last_message - self.first_message).round('S')
#         if skip_days and not p_from:
#             self.first_message = pd.Timestamp(arrow.get(self.first_message).shift(days=skip_days).timestamp()*1e9)
#             self._messages = self._messages[self._messages['timestamp'] > int(arrow.get(self.first_message).shift(days=skip_days).timestamp()*1e3)]
#         self.count_messages = self._messages.shape[0]

#     def msgtxt(self, msg, idx=0):
#         return f"{idx:>06} {msg['severity']} {msg['timestamp']} {pd.to_datetime(int(msg['timestamp'])*1e6).strftime('%d.%m.%Y %H:%M:%S')}  {msg['name']} {msg['message']}"

#     def save_messages(self, fn):
#         with open(fn, 'w') as f:
#             for index, msg in self._messages.iterrows():
#                 f.write(self.msgtxt(msg, index)+'\n')
#                 #f.write(f"{index:>06} {msg['severity']} {msg['timestamp']} {pd.to_datetime(int(msg['timestamp'])*1e6).strftime('%d.%m.%Y %H:%M:%S')}  {msg['name']} {msg['message']}\n")
#                 if 'associatedValues' in msg:
#                     if msg['associatedValues'] == msg['associatedValues']:  # if not NaN ...
#                         f.write(f"{pf(msg['associatedValues'])}\n\n")

#     def save_runlog(self, fn):
#         if len(self._runlog):
#             with open(fn, 'w') as f:
#                 for line in self._runlog:
#                     f.write(line + '\n')

#     ### die Finite State Machine selbst:
#     #1225 Service selector switch Off
#     #1226 Service selector switch Manual
#     #1227 Service selector switch Automatic
#     def _fsm_Service_selector(self, msg):
#         if msg['name'] == '1225 Service selector switch Off'[:4]:
#             self.act_service_selector = 'OFF'
#         if msg['name'] == '1226 Service selector switch Manual'[:4]:
#             self.act_service_selector = 'MANUAL'
#         if msg['name'] == '1227 Service selector switch Automatic'[:4]:
#             self.act_service_selector = 'AUTO'

#     def _fsm_Operating_Cycle(self, actstate, act_transition_time, newstate, new_transition_time, duration, msg):
#         def _to_sec(time_object):
#             return float(time_object.seconds) + float(time_object.microseconds) / 1e6
#         # Start Preparatio => the Engine ist starting
#         if self.current_state == 'startpreparation':
#             # apends a new record to the Starts list.
#             self._starts.append({
#                 'run2':False,
#                 'no':self._starts_counter,
#                 'success': False,
#                 'mode':self.act_service_selector,
#                 'starttime': new_transition_time,
#                 'endtime': pd.Timestamp(0),
#                 'cumstarttime': pd.Timedelta(0),
#                 'timing': {},
#                 'alarms': [],
#                 'warnings': [],
#                 'maxload': np.nan,
#                 'ramprate': np.nan
#             })
#             self._starts_counter += 1 # index for next start
#             # indicate a 
#             self._in_operation = 'on'
#             self._timer = pd.Timedelta(0)
#         elif self._in_operation == 'on': # and actstate != FSM.initial_state:
#             self._timer = self._timer + duration

#             if actstate in self._starts[-1]: # add all duration a start is in a certain state ( important if the engine switches back and forth between states, e.g. Forsa Hartmoor M4, 18.1.2022 fff)
#                 self._starts[-1][actstate] += _to_sec(duration)
#             else:
#                 self._starts[-1][actstate] = _to_sec(duration) #if actstate != 'targetoperation' else duration.round('S')
            
#             self._starts[-1]['timing']['start_'+ actstate] = act_transition_time 
#             self._starts[-1]['timing']['end_'+ actstate] = new_transition_time 

#             if actstate not in ['targetoperation','rampdown','coolrun','aftercooling']: 
#                 self._starts[-1]['cumstarttime'] = _to_sec(self._timer)

#         # if self.current_state == 'targetoperation':
#         #     if self._in_operation == 'on':
#         #         self._starts[-1]['success'] = True   # wenn der Start bis hierhin kommt, ist er erfolgreich.

#         # Ein Motorlauf(-versuch) is zu Ende. 
#         if self.current_state == 'standstill': #'mode-off'
#         #if actstate == 'loadramp': # übergang von loadramp to 'targetoperation'
#             if self._in_operation == 'on':
#                 self._starts[-1]['endtime'] = new_transition_time
#                 if 'targetoperation' in self._starts[-1]:
#                     #successful if the targetoperation run was longer than specified
#                     self._starts[-1]['success'] = (self._starts[-1]['targetoperation'] > self._successtime) 
#             self._in_operation = 'off'
#             self._timer = pd.Timedelta(0)

#         _logline= {
#             'actstate': actstate,
#             'start_time': act_transition_time.strftime('%d.%m.%Y %H:%M:%S'),
#             'msg': msg['name'] + ' ' + msg['message'],
#             'currenstate': self.current_state,
#             'new_transition_time': new_transition_time.strftime('%d.%m.%Y %H:%M:%S'),
#             'duration': _to_sec(duration),
#             '_timer': _to_sec(self._timer),
#             'starts': len(self._starts),
#             'Successful_starts': len([s for s in self._starts if s['success']]),
#             'operation': self._in_operation,
#             'mode': self.act_service_selector,
#         }
#         self._runlog.append(_logline)
#         #_logtxt = f"{new_transition_time.strftime('%d.%m.%Y %H:%M:%S')} |{actstate:<18} {_to_sec(duration):>10.1f}s {_to_sec(self._timer):>10.1f}s {msg['name']} {msg['message']:<40} {len(self._starts):>3d} {len([s for s in self._starts if s['success']]):>3d} {self._in_operation:>3} {self.act_service_selector:>6} => {self.current_state:<20}"
#         #_logtxt = f"{switch_point.strftime('%d.%m.%Y %H:%M:%S')} |{actstate:<18} {_to_sec(duration):8.1f}s {msg['name']} {msg['message']:<40} {len(self._starts):>3d} {len([s for s in self._starts if s['success']]):>3d} {self._in_operation:>3} {self.act_service_selector:>4} => {self.current_state:<20}"
#         #self._runlog.append(_logtxt)

#     def _collect_data(self, actstate, msg):
#         self._fsm_Service_selector(msg)
#         # collect alarms & warnings vs. Starts
#         if self._in_operation == 'on':
#             if msg['severity'] == 800:
#                 self._starts[-1]['alarms'].append({'state':self.current_state, 'msg': msg})
#             if msg['severity'] == 700:
#                 self._starts[-1]['warnings'].append({'state':self.current_state, 'msg': msg})
#         if self.current_state != actstate:
#             # Timestamp at the time of switching states
#             transition_time = pd.to_datetime(float(msg['timestamp'])*1e6)
#             # How long have i been in actstate ?
#             d_ts = pd.Timedelta(transition_time - self.last_ts) if self.last_ts else pd.Timedelta(0)
#             self._fsm_Operating_Cycle(actstate, self.last_ts, self.current_state, transition_time, d_ts, msg)
#             self.last_ts = transition_time


#     def handle_states(self, lactstate, lcurrent_state, msg):

#         # Sonderbehandlung Ende der Phase loadramp 
#         if self._target_load_message:
#             new_state = self.states[lcurrent_state].send(msg)  # die Message kommt in den messages vor, normal behandeln

#         else: # die 'target load reached' message kommt nicht vor => die Zeit bis Vollast muß in RUN 1 geschätzt werden ...
#             #die FSM hat die Phase 'loadramp' noch nicht erreicht 
#             if self.full_load_timestamp == None or int(msg['timestamp']) < self.full_load_timestamp:
#                 new_state = self.states[lcurrent_state].send(msg)
#             elif int(msg['timestamp']) >= self.full_load_timestamp: # now switch to 'targetoperation'
#                 dmsg = {'name':'9047', 'message':'Target load reached (calculated)','timestamp':self.full_load_timestamp,'severity':600}
#                 new_state = self.states[lcurrent_state].send(dmsg)
#                 # Inject the message , collect the data
#                 self._collect_data(lactstate, dmsg)
#                 # rest the algorithm for the next cycle.
#                 self.full_load_timestamp = None
#                 lactstate = lcurrent_state

#             # Algorithm to switch from 'loadramp to' 'targetoperation'
#             # direkt bein Umschalten das Ende der Rampe berechnen
#             if lcurrent_state == 'loadramp' and self.full_load_timestamp == None:  
#                 self.full_load_timestamp = int(msg['timestamp']) + self._default_ramp_duration

#         return lactstate, new_state


#     ## FSM Entry Point.
#     def run1(self, enforce=False):
#         if len(self._starts) == 0 or enforce or not ('run2' in self._starts[0]):
#             self._starts = []
#             self._starts_counter = 0
#             for i,msg in tqdm(self._messages.iterrows(), total=self._messages.shape[0], ncols=80, mininterval=1, unit=' messages', desc="FSM"):
#                 actstate = self.current_state
#                 actstate, self.current_state = self.handle_states(actstate, self.current_state, msg)
#                 self._collect_data(actstate, msg)

    # def run2(self, rda):

    #     index_list = []
    #     for n, startversuch in tqdm(rda.iterrows(), total=rda.shape[0], ncols=80, mininterval=1, unit=' starts', desc="FSM Run2"):

    #             ii = startversuch['no']
    #             index_list.append(ii)

    #             if not startversuch['run2']:

    #                 data = dmyplant2.get_cycle_data2(self, startversuch, max_length=None, min_length=None, silent=True)

    #                 if not data.empty:

    #                     pl, _ = dmyplant2.detect_edge_left(data, 'Power_PowerAct', startversuch)
    #                     #pr, _ = detect_edge_right(data, 'Power_PowerAct', startversuch)
    #                     #sl, _ = detect_edge_left(data, 'Various_Values_SpeedAct', startversuch)
    #                     #sr, _ = detect_edge_right(data, 'Various_Values_SpeedAct', startversuch)

    #                     self._starts[ii]['title'] = f"{self._e} ----- Start {ii} {startversuch['mode']} | {'SUCCESS' if startversuch['success'] else 'FAILED'} | {startversuch['starttime'].round('S')}"
    #                     #sv_lines = {k:(startversuch[k] if k in startversuch else np.NaN) for k in filterFSM.vertical_lines_times]}
    #                     sv_lines = [v for v in startversuch[filterFSM.vertical_lines_times]]
    #                     start = startversuch['starttime'];
                        
    #                     # lade die in run1 gesammelten Daten in ein DataFrame, ersetze NaN Werte mit 0
    #                     backup = {}
    #                     svdf = pd.DataFrame(sv_lines, index=filterFSM.vertical_lines_times, columns=['FSM'], dtype=np.float64).fillna(0)
    #                     svdf['RUN2'] = svdf['FSM']

    #                     # intentionally excluded - Dieter 1.3.2022
    #                     #if svdf.at['speedup','FSM'] > 0.0:
    #                     #        svdf.at['speedup','RUN2'] = sl.loc.timestamp() - start.timestamp() - np.cumsum(svdf['RUN2'])['starter']
    #                     #        svdf.at['idle','RUN2'] = svdf.at['idle','FSM'] - (svdf.at['speedup','RUN2'] - svdf.at['speedup','FSM'])
    #                     if svdf.at['loadramp','FSM'] > 0.0:
    #                             calc_loadramp = pl.loc.timestamp() - start.timestamp() - np.cumsum(svdf['RUN2'])['synchronize']
    #                             svdf.at['loadramp','RUN2'] = calc_loadramp

    #                             # collect run2 results.
    #                             backup['loadramp'] = svdf.at['loadramp','FSM'] # alten Wert merken
    #                             self._starts[ii]['loadramp'] = calc_loadramp

    #                     with warnings.catch_warnings():
    #                         warnings.simplefilter("ignore")
    #                         calc_maxload = pl.val
    #                         try:
    #                             calc_ramp = (calc_maxload / self._e['Power_PowerNominal']) * 100 / svdf.at['loadramp','RUN2']
    #                         except ZeroDivisionError as err:
    #                             logging.warning(f"calc_ramp: {str(err)}")
    #                             calc_ramp = np.NaN
    #                         # doppelte Hosenträger ... hier könnte man ein wenig aufräumen :-)
    #                         if not np.isfinite(calc_ramp) :
    #                             calc_ramp = np.NaN

    #                         backup_cumstarttime = np.cumsum(svdf['FSM'])['loadramp']
    #                         calc_cumstarttime = np.cumsum(svdf['RUN2'])['loadramp']
    #                     svdf = pd.concat([
    #                             svdf, 
    #                             pd.DataFrame.from_dict(
    #                                     {       'maxload':['-',calc_maxload],
    #                                             'ramprate':['-',calc_ramp],
    #                                             'cumstarttime':[backup_cumstarttime, calc_cumstarttime]
    #                                     }, 
    #                                     columns=['FSM','RUN2'],
    #                                     orient='index')]
    #                             )
    #                     #display(HTML(svdf.round(2).T.to_html(escape=False)))

    #                     # collect run2 results.
    #                     self._starts[ii]['maxload'] = calc_maxload
    #                     self._starts[ii]['ramprate'] = calc_ramp
    #                     backup['cumstarttime'] = backup_cumstarttime
    #                     self._starts[ii]['cumstarttime'] = calc_cumstarttime

    #                     self._starts[ii]['backup'] = backup
    #                     self._starts[ii]['run2'] = True

    #     return pd.DataFrame([self._starts[s] for s in index_list])
