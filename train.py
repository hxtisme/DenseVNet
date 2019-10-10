import os
from time import time

import torch
from model.vnet import get_net
from data_loader.data_loader import MyDataset
from torch.utils.data import DataLoader
from loss.avg_dice_loss import AvgDiceLoss
from loss.wgt_dice_loss import WgtDiceLoss
from utils import accuracy
from val import dataset_accuracy
from tensorboardX import SummaryWriter

# 超参数
organs_name = ['spleen', 'left kidney', 'gallbladder', 'esophagus',
               'liver', 'stomach', 'pancreas', 'duodenum']

on_server = True
resume_training = False
module_dir = './module/net45-0.712-0.682.pth'

os.environ['CUDA_VISIBLE_DEVICES'] = '0' if on_server is False else '1,2,3'
torch.backends.cudnn.benchmark = True
Epoch = 10
leaing_rate = 1e-4

batch_size = 3 if on_server else 1
num_workers = 2 if on_server else 1
pin_memory = True if on_server else False

# 模型
net = get_net(training=True)
net = torch.nn.DataParallel(net).cuda() if on_server else net.cuda()
if resume_training:
    print('----------resume training-----------')
    net.load_state_dict(torch.load(module_dir))
    net.train()


# 数据
train_ds = MyDataset('csv_files/train_info.csv')
train_dl = DataLoader(train_ds, batch_size, True, num_workers=num_workers, pin_memory=pin_memory)

# 损失函数
# loss_func = AvgDiceLoss()
loss_func = WgtDiceLoss()

# 优化器
opt = torch.optim.Adam(net.parameters(), lr=leaing_rate, weight_decay=0.0005)

# 学习率衰减
lr_decay = torch.optim.lr_scheduler.MultiStepLR(opt, [900])

# 训练
writer = SummaryWriter()
start = time()
for epoch in range(Epoch):
    mean_loss = []
    mean_acc = []

    for step, (ct, seg) in enumerate(train_dl):
        ct = ct.cuda()

        # switch model to training mode, clear gradient accumulators
        net.train()
        opt.zero_grad()

        # forward + backward + optimize
        outputs_stage1, outputs_stage2 = net(ct)
        loss = loss_func(outputs_stage1, outputs_stage2, seg)
        _, acc = accuracy(outputs_stage2.cpu().detach().numpy(), seg.numpy())

        mean_loss.append(loss)
        mean_acc.append(acc)

        loss.backward()
        opt.step()
        lr_decay.step()

        if step % 4 == 0:
            print('epoch:{}, step:{}, loss:{:.3f}, accuracy:{:.3f}, time:{:.3f} min'
                  .format(epoch, step, loss.item(), acc, (time() - start) / 60))

    mean_loss = sum(mean_loss) / len(mean_loss)
    mean_acc = sum(mean_acc) / len(mean_acc)
    writer.add_scalar('train/loss', mean_loss, epoch)
    writer.add_scalar('train/accuracy', mean_acc, epoch)

    # 每十个个epoch保存一次模型参数
    # 网络模型的命名方式为：epoch轮数+本轮epoch的平均loss+本轮epoch的平均acc
    if epoch % 5 is 0:
        # 验证集accuracy
        val_org_acc, val_mean_acc = dataset_accuracy(net, 'csv_files/val_info.csv')
        print('------------------------')
        print('epoch:%d - train loss:%.3f, train accuracy:%.3f, validation accuracy:%.3f, time:%.3f min' %
              (epoch, mean_loss, mean_acc, val_mean_acc, (time() - start) / 60))
        print(' '.join(["%s:%.3f" % (i, j) for i, j in zip(organs_name, val_org_acc)]))
        writer.add_scalar('validation/accuracy', val_mean_acc, epoch)
        print('------------------------')

        torch.save(net.state_dict(), './module/net{}-{:.3f}-{:.3f}.pth'.format(epoch, mean_loss, mean_acc))


writer.close()
