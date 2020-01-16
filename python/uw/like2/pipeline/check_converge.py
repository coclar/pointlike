"""
Run after a successful UWpipeline/job_task

Summarize the execution, 
mostly zipping the files generated by the multiple jobs and submitting follow-up streams
diagnostic plots are now done by the summary task


$Header: /nfs/slac/g/glast/ground/cvs/pointlike/python/uw/like2/pipeline/check_converge.py,v 1.41 2018/01/27 15:38:26 burnett Exp $
"""
import os, sys, glob, zipfile, logging, datetime, argparse, subprocess
import numpy as np
import pandas as pd

from uw.like2 import (tools, maps, seeds,)
from uw.like2.pipeline import (pipe, stream, stagedict, check_ts, )
from uw.utilities import healpix_map


def streamInfo(stream_id ,path='.'):
    si = stream.SubStreamStats(stream_id, path)
    return si.times

def main(args):
    """ 
    """
    def fixpath(s):
        if os.path.exists(s): return s
        r= s.replace('/a/wain025/g.glast.u55','/afs/slac/g/glast/groups')
        assert os.path.exists(r), 'paths do not exist: %s or %s' % (s,r)
        return r
    pointlike_dir=fixpath(args.pointlike_dir) # = os.environ.get('POINTLIKE_DIR', '.')
    skymodel     =fixpath(args.skymodel) # = os.environ.get('SKYMODEL_SUBDIR', sys.argv[1] )
    
    stream_id = args.stream 
    stagelist = args.stage
    if hasattr(stagelist, '__iter__'): stagelist=stagelist[0] #handle local or from uwpipeline.py
   
    #piperaise Exception('killing this sequence!')
    absskymodel = os.path.join(pointlike_dir, skymodel)

    def make_zip(fname,  ext='pickle', select=None):
        """fname : string
                name of a folder containing files to be zipped 
        """
        if os.path.exists(fname+'zip') and \
           os.path.getmtime(fname) < os.path.getmtime(fname+'.zip'):
            print ('Not updating %s' % fname+'.zip')
            return

        if select is not None:
            ff = glob.glob(os.path.join(absskymodel, select))
        else:
            ff = glob.glob(os.path.join(absskymodel, fname, '*.'+ext))
        if len(ff)==0:
            print ('no files found to zip in folder %s' %fname)
            return
        if len(ff)!=1728 and fname=='pickle' and not stagelist.startswith('addseeds'):
            raise Exception('Stage {}: Found {} pickle files, expected 1728'.format(stagelist, len(ff)))
        print ('found %d *.%s in folder %s ...' % ( len(ff),ext, fname,) ,)
        with zipfile.ZipFile(os.path.join(absskymodel, fname+'.zip'), 'w') as z:
            for filename in ff:
                z.write( filename, os.path.join(fname,os.path.split(filename)[-1]))
        print (' zipped into file %s.zip' %fname)
        
    def create_stream(newstage, job_list=None):
        if job_list is None:
            job_list = stagedict.stagenames[newstage].get('job_list', 'job_list')
        print ('Starting stage {} with job_list {}'.format( newstage, job_list))
        ps = stream.PipelineStream()
        ps(newstage, job_list=job_list, test=False)

    next = args.next
    print ('next: {}'.format(next))
    if not args.test:
        tee = tools.OutputTee(os.path.join(absskymodel, 'summary_log.txt'))

    if os.path.exists(str(stream_id)):
        print ('Abort since detected file with stream name')
        raise Exception('Abort since detected file with stream name')
    if stream_id!=-1:
        streamInfo(stream_id, absskymodel)

    os.chdir(absskymodel) # useful for diagnostics below
    current = str(datetime.datetime.today())[:16]
    print ('\n%s stage %s stream %s model %s ' % (current, stagelist, stream_id,  absskymodel))
    if os.path.exists('failed_rois.txt'):
       
       failed = sorted(map(int, open('failed_rois.txt').read().split()))
       print ('failed rois %s' % failed)
       raise Exception('failed rois %s' % failed)

    t = stagelist.split(':',1)
    if len(t)==2:
        stage, nextstage = t 
    else: stage,nextstage = t[0], None

    ss = stage.split('_')
    stage = ss[0]
    stage_args = ss[1:] if len(ss)>1 else ['none']
    next_stage = stagedict.stagenames[stage].get('next', None)

    
    # always update the pickle for the ROIs, if changed
    make_zip('pickle')
    
    if stage=='update' or  stage=='betafix':
        logto = open(os.path.join(absskymodel,'converge.txt'), 'a')
        qq=pipe.check_converge(absskymodel, tol=12, log=logto)
        r = pipe.roirec(absskymodel)
        q = pipe.check_converge(absskymodel, tol=12 , add_neighbors=False)
        open('update_roi_list.txt', 'w').write('\n'.join(map(str, sorted(qq))))
        if stage_args[0]!='only' and stage_args[0]!='associations':
            if  len(q)>1:
                if len(qq)> 200:
                    create_stream('update')
                else:
                    create_stream('update', job_list='$SKYMODEL_SUBDIR/update_roi_list.txt')
            else:
                model_name = os.getcwd().split('/')[-1]
                create_stream('finish' if model_name.find('month')<0 else 'finish_month')
            
    elif stage=='sedinfo':
        make_zip('sedinfo')
        make_zip('sedfig','png')

    elif stage=='create' or stage=='create_reloc':
        # always do one more stream after initial
        if nextstage is None:
            create_stream('update_full') # always start an update

    elif stage=='diffuse':
        make_zip('galfit_plots', 'png')
        make_zip('galfits_all')

    elif stage=='isodiffuse':
        make_zip('isofit_plots', 'png')
        make_zip('isofits')

    elif stage=='limb':
        make_zip('limb')

    #elif stage=='finish' or stage=='counts':

    elif stage=='tables':
        if len(stage_args)>0:
            names = 'ts kde'.split() if stage_args==['none'] else stage_args

        if 'ts' in names:
            if not os.path.exists('hptables_ts_kde_512.fits'):
                healpix_map.assemble_tables(names)
            modelname = os.getcwd().split('/')[-1]
            check_ts.pipe_make_seeds( modelname, 'seeds_ts.txt')
            if next_stage is not None:
                create_stream(next_stage)
        elif 'hard' in names:
            healpix_map.assemble_tables('hard')
            modelname = os.getcwd().split('/')[-1]
            check_ts.pipe_make_seeds( modelname, 'seeds_hard.txt')
            if next_stage is not None:
                create_stream(next_stage)
        elif 'mspsens' in names:
            healpix_map.assemble_tables(names, nside=256) # assume nside
        elif 'all' in names:
            tsmin=16
            print ('Performing analysis of tables_all, with tsmin={}'.format(tsmin))
            mm = maps.MultiMap()
            print (mm.summary())
            mm.write_fits()
            seeds.create_seedfiles(mm, seed_folder='seeds', tsmin=tsmin)
        else:
            raise Exception( 'Unexpected table name: {}'.format(names))

    elif stage=="sourcefinding":
        nside=256
        maps.nside=nside
        tsmin=12
        print ('Performing analysis of tables_all, with tsmin={}'.format(tsmin))
        mm = maps.MultiMap(nside=nside)
        print (mm.summary())
        mm.write_fits()
        seeds.create_seedfiles(mm, seed_folder='seeds', tsmin=tsmin, nside=nside)


    elif stage=='ptables':
        names = ['tsp'] 
        healpix_map.assemble_tables(names)

    elif stage=='pulsar':
        healpix_map.assemble_tables(['pts'])
        
    elif stage=='seedcheck':
        key = stage_args[0]
        print ('Processing seedcheck with key %s' % key)
        make_zip('seedcheck' , select=None if key=='none' else 'seedcheck_%s/*' %key)
        if key=='pgw': # processing a month
            seedcheck.SeedCheck().all_plots();
            create_stream('update_seeds')

    elif stage=='sourcefinding':
        raise Exception('place holder -- will call code for inclusive template ts maps')
        
    else: # catch fluxcorr, any others like
        # also perhaps start another stage

        if os.path.exists(stage):
            make_zip(stage)
        if next_stage is not None:
            create_stream(next_stage)
        else:
            print ('stage %s not recognized for summary'%stage )
    if not args.test:
        if nextstage:
            create_stream(nextstage)
        tee.close()

if __name__=='__main__':
    parser = argparse.ArgumentParser(description='Run after a set of pipeline jobs, check status, accumulate info')
    parser.add_argument('--stage', default=os.environ.get('stage', '?'), help='the stage indentifier(s)')
    parser.add_argument('--pointlike_dir', default=os.environ.get('POINTLIKE_DIR', '.'),
            help='top level folder with pointlike')
    parser.add_argument('--skymodel', default= os.environ.get('SKYMODEL_SUBDIR', '.'),
            help='folder, from pointlike_dir, to the skymodel. Default $SKYMODEL_SUBDIR, set by pipeline')
    parser.add_argument('--stream', default = os.environ.get('PIPELINE_STREAM', '0'),
            help='stream number')
    parser.add_argument('--test', action='store_false', help='test mode') ######################
    args = parser.parse_args()
    main(args)

