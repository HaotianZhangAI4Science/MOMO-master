import argparse
import os
from tensorboardX import SummaryWriter
# import tensorflow as tf
import time

from models import CDDDModel

from NSGA2 import *
from property import *
from warnings import simplefilter
from Pure_diversity import *
from pandas import DataFrame
simplefilter(action="ignore", category=FutureWarning)
import pygmo as pg
# Suppress warnings
# tf.logging.set_verbosity(tf.logging.ERROR)
# RDLogger.logger().setLevel(RDLogger.CRITICAL)


if __name__ == "__main__":
    # 20
    parser = argparse.ArgumentParser()
    parser.add_argument('-s', '--seed', type=int, default=None,
                        help='Random seed.')

    # parser.add_argument('seq', type=str, help='Sequence to optimize.')
    parser.add_argument("--smile_path", default="qed_test.csv")
    parser.add_argument("--seq", default='opti')
    parser.add_argument("--opt", default='qedgsksa', choices=['qed', 'logP', 'qedplogp','qeddrd', 'qedjnksa', 'qedgsksa'])
    args = parser.parse_args()

    if args.opt == 'qed':
        ff = ff_qed
        fitness = fitness_qed
        tre_pre = 0.9
        tre_sim = 0.4
        col = ['SMILES', 'mol_id', 'qed', 'sim']
    elif args.opt == 'logP':
        ff = ff_plogp
        tre_sim = 0.4
        fitness = fitness_plogp
    elif args.opt == 'drd':
        ff = ff_drd
        fitness = fitness_drd
        tre_pre = 0.5
        tre_sim = 0.3
        col = ['SMILES', 'mol_id', 'drd', 'sim']
    elif args.opt == 'qedgsksa':
        ff = ff_qedgsksa
        fitness = fitness_qedgsksa
        tres = [0.8, 0.3, 0.5, 0.8]
        col = ['SMILES', 'mol_id', 'qed', 'sim', 'gskb', 'sa_nom']


    # device = 'cuda'
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    print('device:', device)
    if args.seed is not None:
        np.random.seed(args.seed)
        # tf.set_random_seed(args.seed)
        torch.manual_seed(args.seed)


    ######加载预训练的CDDD
    model = CDDDModel()
    # canonicalize
    data = pd.read_csv('./data/gsk3_test.csv').values
    orismi_all = pd.read_csv('./data/oripops/QMO_task4_mol400_optsmiles.csv').values
    #orismi_all = -1 * np.ones((4, 4))
    #fit_mol = []

    smi_iter_last = []  # 保留所有分子最后一代，一个分子npop个子代
    HV=[]
    PD=[]
    runtime = []
    #r_time=[]#保留所有分子restart次数
    # 参数设置
    pern = 300     # 扰动隐向量的个数，扰动多个然后排序选npop个
    sita = 0.5      #扰动程度的参数
    nPop = 100      #种群个体数
    pc = 1          #交叉率
    pm = 0.5        #变异率
    nChr = 512       #染色体长度
    lb = -1         #下界
    rb = 1          #上界
    d = 0.25         #线性交叉参数
    nIter = 100      #迭代次数
    m = 5           # 变异个数
    restart = 1
    SR = 0          # 记录优化成功的分子数0.9，0.4
    t1 = time.time()# 开始时间

    a = list(range(200, 381, 20))  # 180
    b = list(range(220, 401, 20))  # 200
    #a=[0]
    #b=[2]
    for num in range(len(a)):
        mm1 = a[num]
        mm2 = b[num]
        smi_iter_all = [['SMILES', 'mol_id','iter', 'qed', 'sim', 'gskb', 'sa_nom']]
        for i in range(mm1,mm2):
            nn = i
            aa = orismi_all[:, 1] == i
            index = np.where(aa == 1)
            orismi = orismi_all[index]  # 提取指定分子的初始种群
            #一个分子SMILES序列
            smiles = data[i][0]
            print(smiles)
            # 一个分子SMILES序列
            mol_0 = Chem.MolFromSmiles(smiles)
            seq = Chem.MolToSmiles(mol_0)
            #print(seq)
            run_str = 'optiza'
            results_dir = os.path.join('results', args.seq)
            os.makedirs(results_dir, exist_ok=True)
            writer = SummaryWriter(os.path.join('runs', args.seq, run_str))
        #调试

            #z_0 = model.encode_latent_mean([seq]).detach()  # 对分子序列进行编码
            z_0 = model.encode(smiles) # 对分子序列进行编码
            fp_0 = morgan_fingerprint(Chem.MolFromSmiles(seq))  # 分子序列的摩根指纹，用于计算相似性
            fits_0 = ff(seq, mol_0, fp_0)
            print(fits_0)
            #QMO初始
            pops_smi = np.zeros((z_0.shape[0] * orismi.shape[0], z_0.shape[1]))
            nonid = []
            mol = []
            for i in range(orismi.shape[0]):
                try:
                    pops_smi[i] = model.encode(orismi[i][0])
                    mol1 = Chem.MolFromSmiles(orismi[i][0])
                    if mol1 is not None:
                        mol.append(mol1)
                    else:
                        nonid.append(i)
                except:
                    nonid.append(i)
            pops_smi_val = np.delete(pops_smi, nonid, 0)
            orismi_val = np.delete(orismi, nonid, 0)
            orismi_val = orismi_val[:,0]
            fits_pops = fitness(orismi_val, mol, fp_0)

            r = 1#重启

            while r <= restart:
                hv=[]
                pure_d=[]
                t2 = time.time()  # 编码前时间
                ####### 对隐向量加入高斯噪声进行扰动
                dispop = np.zeros((z_0.shape[0] * pern, z_0.shape[1]))
                gauss = np.random.normal(0, 1, (pern, z_0.shape[1]))
                dispop[:pern] = gauss * sita + z_0.numpy()
                dispop = torch.from_numpy(dispop)
                # print(newvec)
                # 生成pern个新子代，array（pern，56）
                # 解码序列
                dismol, dissmiles = model.decode(dispop)
                disfits = fitness(dissmiles, dismol, fp_0)  # 适应度计算
                disfits[np.isnan(disfits)] = 0

                ranks = nonDominationSort(dispop, disfits)  # 非支配排序，对潜向量还是分子？？
                distances = crowdingDistanceSort(dispop, disfits, ranks)  # 拥挤度
                if len(fits_pops)==0:
                    pops, fits, smis = select1(nPop, dispop, disfits, ranks, distances, dissmiles)
                else:
                    pops, fits, smis = optSelect_uni(dispop, disfits, pops_smi_val, fits_pops, nPop, dissmiles, orismi_val)

                iter = 1
                while iter <= nIter:
                    # 进度条
                    print("【进度】【{0:20s}】【正在进行{1}代...】【共{2}代】". \
                          format('▋' * int(iter / nIter * 20), iter, nIter), end='\r')
                    #交叉变异
                    #print(fits)
                    chrpops = crossover_2(pops, pc, d, lb, rb)#混合线性交叉
                    chrpops = mutate(chrpops, pm, nChr,m) # 变异产生子种群
                    # 解码子代种群分子
                    #[chrsmiles, nonid] = decode_from_jtvae(chrpops, opts, model)
                    chrpops = torch.from_numpy(chrpops)
                    chrmol, chrsmiles = model.decode(chrpops)  ###解码潜在表示，分子和SMILES
                    #print('chrsmiles:', chrsmiles)
                    chrfits = fitness(chrsmiles, chrmol, fp_0)
                    chrfits = np.nan_to_num(chrfits)
                    # 从原始种群和子种群中筛选nPop个
                    pops, fits, smis = optSelect_uni(pops, fits, chrpops, chrfits, nPop, smis, chrsmiles)
                    unique_smiles_iter = []  # 储存当前分子唯一的SMILES,可放在重启前
                    for i in range(len(smis)):
                        if smis[i] not in unique_smiles_iter:
                            unique_smiles_iter.append(smis[i])
                            tuple = [smis[i], nn, iter, fits[i][0], fits[i][1], fits[i][2], fits[i][3]]
                            smi_iter_all.append(tuple)
                    print(iter)
                    iter = iter + 1

                    if len(fits) == 0:
                        dominated_hypervolume = 0
                        pure_div = 0
                    else:
                        dominated_hypervolume = pg.hypervolume(np.array([-1.0 * fit for fit in fits if
                                                                         (fit>=[0,0,0,0]).all()])).compute(
                            np.zeros(len(fits[0])))
                        pure_div = PD_cal(np.array([fit for fit in fits if (fit>=[0,0,0,0]).all()]))
                    hv.append((dominated_hypervolume))
                    pure_d.append(pure_div)
                rr = []  # 储存endfits是否满足>=0.9,>=0.4，True=1，False=0
                for i in range(len(smis)):
                    rr.append((np.array(fits[:][i]) >= tres).all())
                if 1 in rr:
                    r = restart + 1
                    SR = SR + 1
                else:
                    r = r + 1
                print('restart_run:', r)

            HV.append(hv)
            PD.append(pure_d)
            pops = torch.from_numpy(pops)
            endmol, endsmiles = model.decode(pops)
            endsmiles = np.array(endsmiles)
            #print('endsmiles:', endsmiles)
            #print(endsmiles)
            ranks = nonDominationSort(endsmiles, fits)  # 非支配排序
            distances = crowdingDistanceSort(endsmiles, fits, ranks)  # 拥挤度
            paretoendsmiles = endsmiles[ranks == 0]
            paretoFits = fits[ranks == 0]
            endsmiles = np.array(paretoendsmiles)
            endfits = paretoFits
            #print(endfits)
            t3 = time.time()  # 一个分子重启动进化后时间
            time_1 = (t3 - t2) / 60  # 扰动个数
            print('run time:', time_1)
            runtime.append((time_1))

            unique_smiles = []  # 储存当前分子唯一的SMILES,可放在重启前
            for i in range(len(endsmiles)):
                if endsmiles[i] not in unique_smiles:  # and endfits[i][1] >= 0.4 and endfits[i][0] >= 0.9
                    unique_smiles.append(endsmiles[i])
                    tuple = (endsmiles[i], nn, paretoFits[i][0], paretoFits[i][1],paretoFits[i][2], paretoFits[i][3])
                    smi_iter_last.append(tuple)
            print('restart_run:', r)

            result = [nn - a[0] + 1, SR]
            print('result-all,SR:', result)
            print('save mol:', nn)


            np.savetxt('./results/task4/MOMO_task4_mol'+str(a[0])+str(b[-1])+'_HV.txt', HV, fmt='%s')
            np.savetxt('./results/task4/MOMO_task4_mol' + str(a[0]) + str(b[-1]) + '_PD.txt', PD, fmt='%s')
            df_to_save_A_to_B = DataFrame(smi_iter_last, columns=col)
            df_to_save_A_to_B.to_csv('./results/task4/MOMO_task4_mol'+str(a[0])+str(b[-1])+'_endsmiles.csv', index=False)
            #df_to_save_A_to_B = DataFrame(smi_iter_all, columns=['SMILES', 'mol_id','iter', 'qed', 'sim', 'gskb', 'sa_nom'])
            #df_to_save_A_to_B.to_csv('./results/task4/MOMO_task4_mol'+str(mm1)+str(mm2)+'_iter.csv', index=False)
            np.savetxt('./results/task4/MOMO_task4_mol' + str(mm1)+str(mm2) + '.txt', smi_iter_all, fmt='%s')
            np.savetxt('./results/task4/MOMO_task4_mol'+str(a[0])+str(b[-1])+'.txt', result, fmt='%s')
            np.savetxt('./results/task4/MOMO_task4_mol'+str(a[0])+str(b[-1])+'_runtime.txt', runtime, fmt='%s')
    # 从原始种群和子种群中筛选nPop个
'''
'''
'''
    pern = 3  # 扰动隐向量的个数，扰动多个然后排序选npop个
    sita = 0.5
    nPop = 2
    pc = 1
    pm = 0.1
    nChr = 56
    lb = -2
    rb = 2
    d = 0.25
    nIter = 2
    endsmiles, fits, fits_0 = optimize_EC(model, seq, pern, sita, nPop, pc, pm, nChr, lb, rb, d, nIter)

'''




# writer.close()